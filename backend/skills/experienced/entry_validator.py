# version: 1.0.0
# changelog:
#   1.0.0 - 2026-05-29 - Initial implementation of EntryValidator (CBD v2.2)

"""
COMPONENT: EntryValidator
CBD Identity:
  Name: EntryValidator
  Reason: Enforce quality gates — no partial entries allowed into CONFIRMED or STABLE
  Logical Function: Validation / Quality Gate

IN-Schema:  Full ExperienceEntry object
OUT-Schema: { valid, missing_blocks, failed_rules }
Error-Schema: { error_code: EV_BLOCK_INCOMPLETE|EV_RULE_VIOLATION|EV_STATUS_INVALID, message }

Failure Map:
  - All validation logic in try-catch
  - Returns ALL failures at once, not one at a time
  - Never raises to caller
"""
import logging
import re
from .models import ExperienceEntry, VALID_STATUSES, VALID_TRANSITIONS, VALID_CATEGORIES, VALID_SEVERITIES

logger = logging.getLogger("experienced.validator")


class EntryValidator:
    """
    Gates CONFIRMED/STABLE status — all five blocks must be complete and pass rules.
    Trace points: validation_pass, validation_fail, block_missing
    """

    def validate(self, entry: ExperienceEntry, target_status: str = None) -> dict:
        """
        Validate an ExperienceEntry.
        Returns OUT-Schema dict: { valid, missing_blocks, failed_rules }
        Never raises.
        """
        missing_blocks = []
        failed_rules = []

        try:
            # ── Block presence checks ──────────────────────────────────────────
            if not entry.title or not entry.title.strip():
                missing_blocks.append("identity.title")

            if not entry.category:
                missing_blocks.append("identity.category")

            if not entry.severity:
                missing_blocks.append("identity.severity")

            if not entry.date_encountered:
                missing_blocks.append("identity.date_encountered")

            # Environment block
            env = entry.environment
            if not env:
                missing_blocks.append("environment")
            else:
                if not env.os:
                    missing_blocks.append("environment.os")
                if not env.runtime_version:
                    missing_blocks.append("environment.runtime_version")
                if not env.framework_version:
                    missing_blocks.append("environment.framework_version")

            # Discovery block
            disc = entry.discovery
            if not disc:
                missing_blocks.append("discovery")
            else:
                if not disc.symptom or not disc.symptom.strip():
                    missing_blocks.append("discovery.symptom")
                if not disc.discovery_path or not disc.discovery_path.strip():
                    missing_blocks.append("discovery.discovery_path")

            # Root cause block
            rc = entry.root_cause
            if not rc:
                missing_blocks.append("root_cause")
            else:
                if not rc.statement or not rc.statement.strip():
                    missing_blocks.append("root_cause.statement")
                if not rc.explanation or not rc.explanation.strip():
                    missing_blocks.append("root_cause.explanation")
                if not rc.trigger_conditions or not rc.trigger_conditions.strip():
                    missing_blocks.append("root_cause.trigger_conditions")

            # Solution block
            sol = entry.solution
            if not sol:
                missing_blocks.append("solution")
            else:
                if not sol.fix or not sol.fix.strip():
                    missing_blocks.append("solution.fix")
                if not sol.why_it_works or not sol.why_it_works.strip():
                    missing_blocks.append("solution.why_it_works")

            # Prevention block
            prev = entry.prevention
            if not prev:
                missing_blocks.append("prevention")
            else:
                if not prev.detection_hints:
                    missing_blocks.append("prevention.detection_hints")
                if not prev.recommended_checks:
                    missing_blocks.append("prevention.recommended_checks")

            # ── Quality rules ──────────────────────────────────────────────────

            # Rule 1: symptom must be verbatim (min 20 chars)
            if disc and disc.symptom and len(disc.symptom.strip()) < 20:
                failed_rules.append({
                    "rule": "symptom_verbatim_length",
                    "field": "discovery.symptom",
                    "reason": f"Symptom must be at least 20 characters (verbatim). Got {len(disc.symptom.strip())}."
                })

            # Rule 2: root_cause.statement must be a single sentence (no newlines, ends in .)
            if rc and rc.statement:
                stmt = rc.statement.strip()
                if "\n" in stmt or stmt.count(". ") > 1:
                    failed_rules.append({
                        "rule": "root_cause_single_sentence",
                        "field": "root_cause.statement",
                        "reason": "Must be exactly one sentence. No newlines, at most one period."
                    })

            # Rule 3: environment must use exact version strings — no wildcard patterns like '3.x' or '2.*'
            # Pattern: digit(s) followed by .x or .* — e.g. "3.x", "1.2.*", "3.X"
            _wildcard_pat = re.compile(r'\d+\.(?:x|X|\*)|\*\.\d')
            if env and env.runtime_version:
                if _wildcard_pat.search(env.runtime_version) or env.runtime_version.strip() in ("x", "*"):
                    failed_rules.append({
                        "rule": "exact_version_required",
                        "field": "environment.runtime_version",
                        "reason": "Must be an exact version string. No wildcards (3.x, 2.*, etc.)."
                    })
            if env and env.framework_version:
                if _wildcard_pat.search(env.framework_version) or env.framework_version.strip() in ("x", "*"):
                    failed_rules.append({
                        "rule": "exact_version_required",
                        "field": "environment.framework_version",
                        "reason": "Must be an exact version string. No wildcards (3.x, 2.*, etc.)."
                    })

            # Rule 4: category must be a valid enum value
            if entry.category and entry.category not in VALID_CATEGORIES:
                failed_rules.append({
                    "rule": "valid_category",
                    "field": "category",
                    "reason": f"'{entry.category}' is not a valid category. Valid: {sorted(VALID_CATEGORIES)}"
                })

            # Rule 5: severity must be valid
            if entry.severity and entry.severity not in VALID_SEVERITIES:
                failed_rules.append({
                    "rule": "valid_severity",
                    "field": "severity",
                    "reason": f"'{entry.severity}' is not valid. Valid: {sorted(VALID_SEVERITIES)}"
                })

            # Rule 6: status transition order (if target_status provided)
            if target_status:
                current = entry.status
                if (current, target_status) not in VALID_TRANSITIONS:
                    failed_rules.append({
                        "rule": "valid_lifecycle_transition",
                        "field": "status",
                        "reason": f"Transition {current} → {target_status} is not allowed."
                    })

            valid = not missing_blocks and not failed_rules

            if valid:
                logger.info(f"validation_pass exp_id={entry.exp_id} target_status={target_status}")
            else:
                logger.warning(
                    f"validation_fail exp_id={entry.exp_id} "
                    f"missing={missing_blocks} rules={[r['rule'] for r in failed_rules]}"
                )

            return {
                "valid": valid,
                "missing_blocks": missing_blocks,
                "failed_rules": failed_rules,
            }

        except Exception as e:
            logger.error(f"EntryValidator crash: {e}", exc_info=True)
            return {
                "valid": False,
                "missing_blocks": [],
                "failed_rules": [{"rule": "validator_internal_error", "field": None, "reason": str(e)}],
                "error_code": "EV_RULE_VIOLATION",
            }
