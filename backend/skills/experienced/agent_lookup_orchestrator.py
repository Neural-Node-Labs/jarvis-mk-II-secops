# version: 1.0.0
# changelog:
#   1.0.0 - 2026-05-29 - Initial AgentLookupOrchestrator (CBD v2.2)

"""
COMPONENT: AgentLookupOrchestrator
CBD Identity:
  Name: AgentLookupOrchestrator
  Reason: Mandatory Phase 0 entry point — search first, act second. Never skip.
  Logical Function: Orchestration / Decision

IN-Schema:  { task_context: { symptom_observed, error_message?, environment } }
OUT-Schema: { decision: USE_EXISTING|CREATE_NEW|DRAFT_PENDING, matched_entry?, draft_id?, action_taken }
Error-Schema: { error_code: ALO_SEARCH_FAILED|ALO_WRITE_FAILED|ALO_VALIDATION_REJECTED, message, partial_state? }

Orchestration Flow:
  1. Call ExperienceSearcher(symptom_observed)
  2. If hit → load entry → return USE_EXISTING
  3. If miss → proceed with task → on resolution create DRAFT
  4. Validate → if valid: CONFIRMED → IndexManager.add() → return CREATE_NEW
  5. If not resolved → return DRAFT_PENDING

Failure Map:
  - Search failure is NON-BLOCKING — agent proceeds and logs
  - Validation rejection keeps entry DRAFT — task not closed until resolved
  - All sub-component calls wrapped in try-catch
"""
import logging
from pathlib import Path
from .experience_reader import ExperienceReader, ExperienceSearcher
from .experience_writer import ExperienceWriter
from .entry_validator import EntryValidator
from .lifecycle_manager import LifecycleManager
from .index_manager import IndexManager, IndexRecord
from .models import ExperienceEntry, utc_now

logger = logging.getLogger("experienced.orchestrator")

EXPERIENCE_LOG = "experienced/experience.log"


def _append_audit_log(base_path: str, entry: str):
    """Append one line to experience.log. Never raises."""
    try:
        log_path = Path(base_path) / "experience.log"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception as e:
        logger.warning(f"experience.log write failed: {e}")


class AgentLookupOrchestrator:
    """
    Coordinates Phase 0 lookup and Phase III entry lifecycle.
    Trace points: lookup_hit, lookup_miss, entry_drafted, entry_confirmed
    """

    def __init__(self, base_path: str):
        self.base_path = base_path
        self.searcher = ExperienceSearcher(base_path)
        self.reader = ExperienceReader(base_path)
        self.writer = ExperienceWriter(base_path)
        self.validator = EntryValidator()
        self.lifecycle = LifecycleManager(base_path)
        self.index = IndexManager(base_path)

    # ── Phase 0: Pre-task Lookup ───────────────────────────────────────────────

    def lookup(self, task_context: dict) -> dict:
        """
        Phase 0 mandatory lookup. Call before any debugging/setup task.
        Returns decision dict. Search failure is non-blocking.
        """
        symptom = task_context.get("symptom_observed", "")
        error_msg = task_context.get("error_message", "")
        query = error_msg or symptom

        ts = utc_now()
        try:
            search_result = self.searcher.search(query, max_results=3)

            if "error_code" in search_result:
                # Non-blocking: log miss and proceed
                _append_audit_log(self.base_path,
                    f"[{ts}] [PHASE-0]  LOOKUP_MISS  query={repr(query[:60])} reason={search_result['error_code']}")
                logger.warning(f"lookup_miss (search failed) query={repr(query[:60])}")
                return {
                    "decision": "CREATE_NEW",
                    "matched_entry": None,
                    "draft_id": None,
                    "action_taken": f"Search unavailable ({search_result['error_code']}). Proceeding without prior art.",
                }

            results = search_result.get("results", [])

            if results:
                top = results[0]
                _append_audit_log(self.base_path,
                    f"[{ts}] [PHASE-0]  LOOKUP_HIT   query={repr(query[:60])} matched={top['exp_id']}")
                logger.info(f"lookup_hit exp_id={top['exp_id']} query={repr(query[:60])}")
                # Load the full entry content
                entry_data = self.reader.read(top["exp_id"])
                return {
                    "decision": "USE_EXISTING",
                    "matched_entry": entry_data,
                    "draft_id": None,
                    "action_taken": f"Found existing entry {top['exp_id']}: {top['title']}",
                }

            # No match
            _append_audit_log(self.base_path,
                f"[{ts}] [PHASE-0]  LOOKUP_MISS  query={repr(query[:60])}")
            logger.info(f"lookup_miss query={repr(query[:60])}")
            return {
                "decision": "CREATE_NEW",
                "matched_entry": None,
                "draft_id": None,
                "action_taken": "No matching experience found. Proceed with task and capture findings.",
            }

        except Exception as e:
            logger.error(f"AgentLookupOrchestrator.lookup crash: {e}", exc_info=True)
            # Non-blocking
            return {
                "decision": "CREATE_NEW",
                "matched_entry": None,
                "draft_id": None,
                "action_taken": f"Lookup error (non-blocking): {str(e)}",
                "error_code": "ALO_SEARCH_FAILED",
            }

    # ── Phase III: Capture New Knowledge ─────────────────────────────────────

    def capture(self, entry: ExperienceEntry) -> dict:
        """
        Create a DRAFT entry, validate, promote to CONFIRMED, update index.
        Returns decision dict. Never raises.
        """
        ts = utc_now()
        try:
            # Ensure index exists
            self.index.execute("init")

            # Step 1: Write DRAFT
            write_result = self.writer.write(entry)
            if "error_code" in write_result:
                return {
                    "error_code": "ALO_WRITE_FAILED",
                    "message": write_result.get("message"),
                    "partial_state": write_result,
                }

            exp_id = write_result["exp_id"]
            _append_audit_log(self.base_path, f"[{ts}] [PHASE-3]  ENTRY_DRAFTED  exp_id={exp_id}")
            logger.info(f"entry_drafted exp_id={exp_id}")

            # Step 2: Validate
            validation = self.validator.validate(entry, target_status="CONFIRMED")

            if not validation["valid"]:
                _append_audit_log(self.base_path,
                    f"[{ts}] [PHASE-3]  VALIDATION_REJECTED  exp_id={exp_id}")
                logger.warning(f"entry stays DRAFT — validation failed exp_id={exp_id}")
                return {
                    "decision": "DRAFT_PENDING",
                    "matched_entry": None,
                    "draft_id": exp_id,
                    "action_taken": "Entry created as DRAFT. Validation failed — resolve issues to promote to CONFIRMED.",
                    "error_code": "ALO_VALIDATION_REJECTED",
                    "validation_result": validation,
                }

            # Step 3: Transition to CONFIRMED
            transition_result = self.lifecycle.transition(exp_id, "CONFIRMED")
            if "error_code" in transition_result:
                return {
                    "decision": "DRAFT_PENDING",
                    "draft_id": exp_id,
                    "action_taken": f"DRAFT created but transition to CONFIRMED failed: {transition_result['message']}",
                    "error_code": "ALO_WRITE_FAILED",
                    "partial_state": transition_result,
                }

            # Step 4: Update index
            record = IndexRecord(
                exp_id=exp_id,
                title=entry.title,
                category=entry.category,
                severity=entry.severity,
                status="CONFIRMED",
                environment_summary=entry.environment.runtime_version if entry.environment else "N/A",
                date_encountered=entry.date_encountered,
                file_path=write_result["file_path"],
            )
            self.index.execute("add", record)

            _append_audit_log(self.base_path,
                f"[{ts}] [PHASE-3]  ENTRY_CONFIRMED  exp_id={exp_id} category={entry.category}")
            logger.info(f"entry_confirmed exp_id={exp_id}")

            return {
                "decision": "CREATE_NEW",
                "matched_entry": {"exp_id": exp_id, "file_path": write_result["file_path"]},
                "draft_id": None,
                "action_taken": f"Entry {exp_id} created and confirmed. Index updated.",
            }

        except Exception as e:
            logger.error(f"AgentLookupOrchestrator.capture crash: {e}", exc_info=True)
            return {
                "error_code": "ALO_WRITE_FAILED",
                "message": str(e),
                "partial_state": None,
            }

    # ── Promote existing DRAFT ────────────────────────────────────────────────

    def promote(self, exp_id: str, new_status: str, superseded_by: str = None) -> dict:
        """Transition an existing entry to a new status and sync the index."""
        try:
            result = self.lifecycle.transition(exp_id, new_status, superseded_by)
            if "error_code" in result:
                return result
            # Sync index
            self.index.execute("rebuild")
            ts = utc_now()
            _append_audit_log(self.base_path,
                f"[{ts}] [PHASE-3]  STATUS_CHANGED  exp_id={exp_id} new_status={new_status}")
            return result
        except Exception as e:
            logger.error(f"promote crash: {e}", exc_info=True)
            return {"error_code": "ALO_WRITE_FAILED", "message": str(e)}
