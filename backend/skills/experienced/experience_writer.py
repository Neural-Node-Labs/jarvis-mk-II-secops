# version: 1.0.0
# changelog:
#   1.0.0 - 2026-05-29 - Initial implementation of ExperienceWriter (CBD v2.2)

"""
COMPONENT: ExperienceWriter
CBD Identity:
  Name: ExperienceWriter
  Reason: Persist a new or updated experience entry as a structured Markdown file
  Logical Function: Write / Persistence

IN-Schema:  NewExperiencePayload (ExperienceEntry)
OUT-Schema: { exp_id, file_path, status, written_at }
Error-Schema: { error_code: EW_VALIDATION_FAILED|EW_WRITE_ERROR|EW_DUPLICATE_ID, message, field }

Failure Map:
  - try-catch on all filesystem writes
  - Validate IN-Schema before any write
  - Atomic write: temp file → rename
  - Reject duplicate IDs
"""
import os
import logging
from pathlib import Path
from .models import ExperienceEntry, next_exp_id, utc_now
from .entry_validator import EntryValidator

logger = logging.getLogger("experienced.writer")

_validator = EntryValidator()


def _render_md(entry: ExperienceEntry) -> str:
    """Render an ExperienceEntry as the five-block Markdown format."""
    env = entry.environment
    disc = entry.discovery
    rc = entry.root_cause
    sol = entry.solution
    prev = entry.prevention

    misleading = "\n".join(f"- {s}" for s in (disc.misleading_signals or [])) or "- None"
    alts = "\n".join(f"- {a}" for a in (sol.alternatives_considered or [])) or "- None"
    hints = "\n".join(f"- {h}" for h in (prev.detection_hints or [])) or "- None"
    checks = "\n".join(f"- {c}" for c in (prev.recommended_checks or [])) or "- None"
    related = "\n".join(f"- {r}" for r in (prev.related_entries or [])) or "- None"
    tools = ", ".join(env.tool_versions) if env.tool_versions else "N/A"

    return f"""# {entry.exp_id} — {entry.title}

**Status**: {entry.status}
**Category**: {entry.category}
**Severity**: {entry.severity}
**Date Encountered**: {entry.date_encountered}

---

## Block 1 — Identity

| Field | Value |
|---|---|
| ID | {entry.exp_id} |
| Title | {entry.title} |
| Category | {entry.category} |
| Severity | {entry.severity} |
| Date | {entry.date_encountered} |
| OS | {env.os} |
| Runtime Version | {env.runtime_version} |
| Framework Version | {env.framework_version} |
| Tool Versions | {tools} |

---

## Block 2 — Discovery

**Symptom (verbatim)**:
```
{disc.symptom}
```

**Discovery Path**: {disc.discovery_path}

**Misleading Signals**:
{misleading}

---

## Block 3 — Root Cause

**Statement**: {rc.statement}

**Explanation**: {rc.explanation}

**Trigger Conditions**: {rc.trigger_conditions}

---

## Block 4 — Solution

**Fix**:
```
{sol.fix}
```

**Why It Works**: {sol.why_it_works}

**Alternatives Considered**:
{alts}

**Side Effects**: {sol.side_effects or "None"}

---

## Block 5 — Prevention

**Detection Hints**:
{hints}

**Recommended Checks**:
{checks}

**Related Entries**:
{related}
"""


class ExperienceWriter:
    """
    Writes experience entries to disk.
    Trace points: entry_created, validation_failed, write_error
    """

    def __init__(self, base_path: str):
        self.base_path = Path(base_path)

    def write(self, entry: ExperienceEntry) -> dict:
        """
        Persist entry to disk. Returns OUT-Schema dict or Error-Schema dict.
        Never raises.
        """
        try:
            self.base_path.mkdir(parents=True, exist_ok=True)

            # Assign ID if not set
            if not entry.exp_id:
                existing = [p.stem for p in self.base_path.glob("EXP-*.md")]
                entry.exp_id = next_exp_id(existing)

            # Reject duplicate — never overwrite
            target = self.base_path / f"{entry.exp_id}.md"
            if target.exists():
                logger.warning(f"Duplicate EXP ID rejected: {entry.exp_id}")
                return {
                    "error_code": "EW_DUPLICATE_ID",
                    "message": f"{entry.exp_id} already exists at {target}. Use LifecycleManager to update status.",
                    "field": "exp_id",
                }

            # Validate before writing
            result = _validator.validate(entry)
            if not result["valid"]:
                logger.warning(f"validation_failed exp_id={entry.exp_id}")
                return {
                    "error_code": "EW_VALIDATION_FAILED",
                    "message": "Entry did not pass validation. Fix all issues before writing.",
                    "field": None,
                    "validation_result": result,
                }

            # Atomic write: temp → rename
            content = _render_md(entry)
            tmp = target.with_suffix(".tmp")
            try:
                tmp.write_text(content, encoding="utf-8")
                tmp.rename(target)
            except Exception as fs_err:
                if tmp.exists():
                    tmp.unlink(missing_ok=True)
                raise fs_err

            written_at = utc_now()
            logger.info(f"entry_created exp_id={entry.exp_id} file={target} status={entry.status}")
            return {
                "exp_id": entry.exp_id,
                "file_path": str(target),
                "status": entry.status,
                "written_at": written_at,
            }

        except Exception as e:
            logger.error(f"write_error exp_id={getattr(entry,'exp_id','?')}: {e}", exc_info=True)
            return {
                "error_code": "EW_WRITE_ERROR",
                "message": str(e),
                "field": None,
            }

    def overwrite(self, entry: ExperienceEntry) -> dict:
        """
        Overwrite an existing entry (used by LifecycleManager for status transitions).
        Atomic write. Never raises.
        """
        try:
            target = self.base_path / f"{entry.exp_id}.md"
            content = _render_md(entry)
            tmp = target.with_suffix(".tmp")
            try:
                tmp.write_text(content, encoding="utf-8")
                tmp.rename(target)
            except Exception as fs_err:
                if tmp.exists():
                    tmp.unlink(missing_ok=True)
                raise fs_err
            logger.info(f"entry_updated exp_id={entry.exp_id} status={entry.status}")
            return {
                "exp_id": entry.exp_id,
                "file_path": str(target),
                "status": entry.status,
                "written_at": utc_now(),
            }
        except Exception as e:
            logger.error(f"overwrite error exp_id={getattr(entry,'exp_id','?')}: {e}", exc_info=True)
            return {"error_code": "EW_WRITE_ERROR", "message": str(e), "field": None}
