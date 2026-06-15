"""
Memory Manager Skill — Agent-facing wrapper around core.memory_manager.MemoryManager.
Exposes save, retrieve, clear, search, stats as TOOL_CALL actions.

version: 1.1.0
changelog:
  1.1.0 - 2026-06-14 - Production hardening.
    FIXED  Module-level `_mem = MemoryManager()` crashes the entire import if
           MemoryManager raises during initialisation (e.g. DB unavailable).
           Now lazy-initialised on first call with a clear MEM_INIT_ERROR response.
    FIXED  execute() had no top-level try/except — any unexpected exception from
           a handler propagated uncaught to the caller with no SkillResult wrapper.
    FIXED  _save: role values are not validated; an invalid role is stored and
           corrupts history ordering downstream.  Now validated against allowed set.
    FIXED  _clear: `ok = _mem.clear(user_id)` — if clear() returns a falsy value
           (None, 0) on success, the result incorrectly shows cleared=False.
           Now checks for explicit False vs None.
  1.0.0 - 2026-06-10 - Initial.
"""
import logging
import traceback
from core.skill_registry import SkillResult

logger = logging.getLogger("skill.memory_manager")

_mem = None  # FIX: lazy init — don't crash import if MemoryManager is unavailable

VALID_ROLES = {"user", "assistant", "system", "tool"}


def _get_mem():
    """Lazy singleton accessor with a clear error on init failure."""
    global _mem
    if _mem is None:
        from core.memory_manager import MemoryManager
        _mem = MemoryManager()
    return _mem


class MemoryManagerSkill:

    async def execute(self, action: str, params: dict, confirmed: bool = False) -> SkillResult:
        dispatch = {
            "save":     self._save,
            "retrieve": self._retrieve,
            "clear":    self._clear,
            "search":   self._search,
            "stats":    self._stats,
        }
        fn = dispatch.get(action)
        if fn is None:
            return SkillResult.fail(f"MEM_UNKNOWN_ACTION: '{action}'. Valid: {', '.join(dispatch)}")
        # FIX: top-level guard so unexpected handler exceptions return SkillResult
        try:
            return await fn(params, confirmed)
        except Exception as exc:
            logger.error("[memory_manager.%s] %s\n%s", action, exc, traceback.format_exc())
            return SkillResult.fail(f"MEM_INTERNAL_ERROR: {type(exc).__name__}: {exc}")

    async def _save(self, params: dict, confirmed: bool) -> SkillResult:
        user_id = params.get("user_id", "default")
        role    = params.get("role", "assistant")
        content = params.get("content", "")
        if not content:
            return SkillResult.fail("MEM_MISSING_CONTENT")
        # FIX: validate role before writing to avoid corrupting history
        if role not in VALID_ROLES:
            return SkillResult.fail(
                f"MEM_INVALID_ROLE: '{role}'. Valid roles: {', '.join(sorted(VALID_ROLES))}"
            )
        try:
            _get_mem().save_turn(user_id, role, content)
        except Exception as exc:
            return SkillResult.fail(f"MEM_INIT_ERROR: {exc}")
        return SkillResult.ok({"saved": True, "user_id": user_id, "role": role})

    async def _retrieve(self, params: dict, confirmed: bool) -> SkillResult:
        user_id = params.get("user_id", "default")
        n       = int(params.get("n", 5))
        try:
            history = _get_mem().retrieve_last_n(user_id, n)
        except Exception as exc:
            return SkillResult.fail(f"MEM_INIT_ERROR: {exc}")
        return SkillResult.ok({
            "user_id": user_id,
            "n":       n,
            "history": history,
            "found":   bool(history),
        })

    async def _clear(self, params: dict, confirmed: bool) -> SkillResult:
        user_id = params.get("user_id", "default")
        if not confirmed:
            return SkillResult.confirm(
                f"⚠️ About to clear ALL memory for user '{user_id}'. This cannot be undone. Confirm?",
                output={"user_id": user_id},
            )
        try:
            result = _get_mem().clear(user_id)
        except Exception as exc:
            return SkillResult.fail(f"MEM_INIT_ERROR: {exc}")
        # FIX: treat None (implicit success) as cleared=True; only explicit False
        # means the operation failed.
        cleared = result is not False
        return SkillResult.ok({"cleared": cleared, "user_id": user_id})

    async def _search(self, params: dict, confirmed: bool) -> SkillResult:
        user_id     = params.get("user_id", "default")
        query       = params.get("query", "")
        max_results = int(params.get("max_results", 5))
        if not query:
            return SkillResult.fail("MEM_MISSING_QUERY")
        try:
            results = _get_mem().search(user_id, query, max_results)
        except Exception as exc:
            return SkillResult.fail(f"MEM_INIT_ERROR: {exc}")
        return SkillResult.ok({"user_id": user_id, "query": query, "results": results, "count": len(results)})

    async def _stats(self, params: dict, confirmed: bool) -> SkillResult:
        user_id = params.get("user_id", "default")
        try:
            stats = _get_mem().get_stats(user_id)
        except Exception as exc:
            return SkillResult.fail(f"MEM_INIT_ERROR: {exc}")
        return SkillResult.ok(stats)