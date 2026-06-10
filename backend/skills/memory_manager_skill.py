"""
Memory Manager Skill — Agent-facing wrapper around core.memory_manager.MemoryManager.
Exposes save, retrieve, clear, search, stats as TOOL_CALL actions.

version: 1.0.0
changelog:
  1.0.0 - 2026-06-10 - Initial. Implements memory-blueprint.md EpisodicStore interface.
"""
import logging
from core.skill_registry import SkillResult
from core.memory_manager import MemoryManager

logger = logging.getLogger("skill.memory_manager")

_mem = MemoryManager()


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
        return await fn(params, confirmed)

    async def _save(self, params: dict, confirmed: bool) -> SkillResult:
        user_id = params.get("user_id", "default")
        role    = params.get("role", "assistant")
        content = params.get("content", "")
        if not content:
            return SkillResult.fail("MEM_MISSING_CONTENT")
        _mem.save_turn(user_id, role, content)
        return SkillResult.ok({"saved": True, "user_id": user_id, "role": role})

    async def _retrieve(self, params: dict, confirmed: bool) -> SkillResult:
        user_id = params.get("user_id", "default")
        n       = int(params.get("n", 5))
        history = _mem.retrieve_last_n(user_id, n)
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
        ok = _mem.clear(user_id)
        return SkillResult.ok({"cleared": ok, "user_id": user_id})

    async def _search(self, params: dict, confirmed: bool) -> SkillResult:
        user_id     = params.get("user_id", "default")
        query       = params.get("query", "")
        max_results = int(params.get("max_results", 5))
        if not query:
            return SkillResult.fail("MEM_MISSING_QUERY")
        results = _mem.search(user_id, query, max_results)
        return SkillResult.ok({"user_id": user_id, "query": query, "results": results, "count": len(results)})

    async def _stats(self, params: dict, confirmed: bool) -> SkillResult:
        user_id = params.get("user_id", "default")
        return SkillResult.ok(_mem.get_stats(user_id))
