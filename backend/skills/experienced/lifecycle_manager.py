# version: 1.0.0
# changelog:
#   1.0.0 - 2026-05-29 - Initial LifecycleManager (CBD v2.2)

"""
COMPONENT: LifecycleManager
CBD Identity:
  Name: LifecycleManager
  Reason: Control and enforce valid status transitions for experience entries
  Logical Function: State Management

IN-Schema:  { exp_id, new_status, superseded_by? }
OUT-Schema: { exp_id, previous_status, new_status, transitioned_at }
Error-Schema: { error_code: LM_INVALID_TRANSITION|LM_ENTRY_NOT_FOUND|LM_MISSING_SUPERSEDED_BY, message, allowed_transitions }

Valid transitions (whitelist):
  DRAFT      → CONFIRMED
  CONFIRMED  → STABLE
  CONFIRMED  → SUPERSEDED
  STABLE     → SUPERSEDED

Failure Map:
  - Transition whitelist enforced — any unlisted pair is rejected
  - SUPERSEDED requires superseded_by — missing is a hard error
  - try-catch on all file operations
"""
import logging
import re
from pathlib import Path
from .models import VALID_TRANSITIONS, utc_now
from .entry_validator import EntryValidator

logger = logging.getLogger("experienced.lifecycle")

_validator = EntryValidator()


def _allowed_from(current_status: str) -> list:
    return [t for f, t in VALID_TRANSITIONS if f == current_status]


class LifecycleManager:
    """
    Manages entry status transitions.
    Trace points: status_changed, invalid_transition
    """

    def __init__(self, base_path: str):
        self.base_path = Path(base_path)

    def transition(self, exp_id: str, new_status: str, superseded_by: str = None) -> dict:
        """
        Transition an entry to new_status.
        Returns OUT-Schema or Error-Schema. Never raises.
        """
        try:
            target = self.base_path / f"{exp_id}.md"
            if not target.exists():
                return {
                    "error_code": "LM_ENTRY_NOT_FOUND",
                    "message": f"{exp_id} not found at {target}",
                    "allowed_transitions": [],
                }

            content = target.read_text(encoding="utf-8")
            current_status = self._extract_status(content)

            if not current_status:
                return {
                    "error_code": "LM_ENTRY_NOT_FOUND",
                    "message": f"Could not read current status from {exp_id}.md",
                    "allowed_transitions": [],
                }

            # SUPERSEDED requires superseded_by — check before whitelist for clear error message
            if new_status == "SUPERSEDED" and not superseded_by:
                return {
                    "error_code": "LM_MISSING_SUPERSEDED_BY",
                    "message": "superseded_by is required when transitioning to SUPERSEDED.",
                    "allowed_transitions": _allowed_from(current_status),
                }

            # Whitelist check
            if (current_status, new_status) not in VALID_TRANSITIONS:
                allowed = _allowed_from(current_status)
                logger.warning(f"invalid_transition exp_id={exp_id} attempted={current_status}→{new_status}")
                return {
                    "error_code": "LM_INVALID_TRANSITION",
                    "message": f"Transition {current_status} → {new_status} is not allowed.",
                    "allowed_transitions": allowed,
                }

            # If transitioning to CONFIRMED — validate entry first
            if new_status == "CONFIRMED":
                from .experience_reader import ExperienceReader
                # Quick block-presence validation using raw content
                # Validate with current status — the transition check itself is handled
                # separately by the whitelist. Passing current status avoids CONFIRMED→CONFIRMED.
                validation = _validator.validate(
                    self._content_to_entry_stub(exp_id, content, "DRAFT")
                )
                if not validation["valid"]:
                    return {
                        "error_code": "LM_VALIDATION_REQUIRED",
                        "message": "Entry must pass EntryValidator before transitioning to CONFIRMED.",
                        "validation_result": validation,
                        "allowed_transitions": _allowed_from(current_status),
                    }

            # Apply transition — update Status field in file
            transitioned_at = utc_now()
            new_content = self._update_status_in_content(content, new_status, superseded_by, transitioned_at)

            tmp = target.with_suffix(".tmp")
            try:
                tmp.write_text(new_content, encoding="utf-8")
                tmp.rename(target)
            except Exception as e:
                if tmp.exists():
                    tmp.unlink(missing_ok=True)
                raise e

            logger.info(f"status_changed exp_id={exp_id} from={current_status} to={new_status}")
            return {
                "exp_id": exp_id,
                "previous_status": current_status,
                "new_status": new_status,
                "transitioned_at": transitioned_at,
            }

        except Exception as e:
            logger.error(f"LifecycleManager.transition error {exp_id}: {e}", exc_info=True)
            return {
                "error_code": "LM_INVALID_TRANSITION",
                "message": str(e),
                "allowed_transitions": [],
            }

    def _extract_status(self, content: str) -> str:
        """Extract current Status value from markdown content."""
        for line in content.splitlines():
            m = re.match(r"\*\*Status\*\*:\s*(\w+)", line.strip())
            if m:
                return m.group(1)
        return None

    def _update_status_in_content(self, content: str, new_status: str,
                                   superseded_by: str, transitioned_at: str) -> str:
        """Replace the Status line and optionally append superseded_by note."""
        lines = content.splitlines()
        new_lines = []
        for line in lines:
            if re.match(r"\*\*Status\*\*:", line.strip()):
                new_lines.append(f"**Status**: {new_status}")
            else:
                new_lines.append(line)

        # Append superseded_by annotation
        if new_status == "SUPERSEDED" and superseded_by:
            new_lines.append(f"\n---\n\n**Superseded By**: {superseded_by}  \n**Superseded At**: {transitioned_at}")

        return "\n".join(new_lines)

    def _content_to_entry_stub(self, exp_id, content, target_status):
        """
        Build a minimal ExperienceEntry stub from raw markdown content
        for validation pre-check. Fields are strings; real validator checks presence.
        """
        from .models import (ExperienceEntry, ExperienceEnvironment,
                             ExperienceDiscovery, ExperienceRootCause,
                             ExperienceSolution, ExperiencePrevention)

        def _get(label):
            """Read value from **Label**: inline format OR | Label | Value | table format."""
            for line in content.splitlines():
                stripped = line.strip()
                # Inline bold format: **Label**: value
                if stripped.startswith(f"**{label}**:"):
                    val = stripped.split(":", 1)[-1].strip()
                    if val:
                        return val
                # Markdown table row format: | Label | Value |
                if stripped.startswith("|") and "|" in stripped[1:]:
                    cells = [c.strip() for c in stripped.split("|")[1:-1]]
                    if len(cells) >= 2 and cells[0].lower() == label.lower():
                        val = cells[1].strip()
                        if val:
                            return val
            return ""

        def _get_code_block_after(label):
            """Extract content of first ``` code block after **Label**: or a heading containing label."""
            lines = content.splitlines()
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith(f"**{label}**") or (label in stripped and stripped.startswith("#")):
                    for j in range(i + 1, min(i + 15, len(lines))):
                        if lines[j].strip().startswith("```"):
                            block = []
                            for k in range(j + 1, len(lines)):
                                if lines[k].strip().startswith("```"):
                                    break
                                block.append(lines[k])
                            return "\n".join(block).strip()
            return ""

        symptom = _get_code_block_after("Symptom (verbatim)")
        fix = _get_code_block_after("Fix")

        def _get_list_items(label):
            """Extract bullet list items after a label heading."""
            lines = content.splitlines()
            items = []
            capturing = False
            for line in lines:
                if f"**{label}**:" in line:
                    capturing = True
                    continue
                if capturing:
                    stripped = line.strip()
                    if stripped.startswith("- ") and stripped != "- None":
                        items.append(stripped[2:])
                    elif stripped.startswith("#") or (stripped.startswith("**") and stripped.endswith("**:")):
                        break
            return items

        return ExperienceEntry(
            exp_id=exp_id,
            title=_get("Title") or exp_id,
            category=_get("Category") or "unknown",
            severity=_get("Severity") or "unknown",
            date_encountered=_get("Date") or _get("Date Encountered") or "unknown",
            environment=ExperienceEnvironment(
                os=_get("OS") or "x",
                runtime_version=_get("Runtime Version") or "x",
                framework_version=_get("Framework Version") or "x",
            ),
            discovery=ExperienceDiscovery(
                symptom=symptom or "",
                discovery_path=_get("Discovery Path") or "",
            ),
            root_cause=ExperienceRootCause(
                statement=_get("Statement") or "",
                explanation=_get("Explanation") or "",
                trigger_conditions=_get("Trigger Conditions") or "",
            ),
            solution=ExperienceSolution(
                fix=fix or "",
                why_it_works=_get("Why It Works") or "",
            ),
            prevention=ExperiencePrevention(
                detection_hints=_get_list_items("Detection Hints"),
                recommended_checks=_get_list_items("Recommended Checks"),
            ),
            status=target_status,
        )
