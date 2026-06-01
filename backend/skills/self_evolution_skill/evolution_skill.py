"""evolution_skill.py — v1.0.0 — SkillRegistry-compatible evolution engine"""

import asyncio, logging, random
from datetime import datetime, timezone
from .session import EvolutionSession, SessionStatus
from .orchestrator import EvolutionOrchestrator

logger = logging.getLogger("self_evolution_skill")

_sessions: dict[str, EvolutionSession] = {}
_orchestrators: dict[str, EvolutionOrchestrator] = {}

def _slug():
    return random.choice(["upgrade","refine","enhance","evolve","augment","optimise"])+"-skill"

def _ws_id():
    n = datetime.now(timezone.utc)
    return f"ws-{n.strftime('%Y%m%d')}-{n.strftime('%H%M')}-{_slug()}"


class SelfEvolutionSkill:
    description = "CBD v2.2 Self-Evolution Orchestrator – executes 10-phase protocol"
    actions = ["start","status","approve","revise","reset","get_logs"]

    async def execute(self, action, params, confirmed=False):
        from core.skill_registry import SkillResult
        try:
            fn = getattr(self, f"_action_{action}", None)
            if not fn:
                return SkillResult(False, None, error=f"Unknown action: {action}")
            return await fn(params)
        except Exception as e:
            logger.error(f"Action {action} failed: {e}", exc_info=True)
            return SkillResult(False, None, error=str(e))

    def _new_session(self, target_skill="", task_desc=""):
        ws_id = _ws_id()
        s = EvolutionSession(workspace_id=ws_id, workspace_path=f"/tmp/evo/{ws_id}",
                             target_skill=target_skill, task_description=task_desc)
        s.add_log("EVOLUTION SESSION STARTING…")
        _sessions[ws_id] = s
        return s

    async def _action_start(self, params):
        from core.skill_registry import SkillResult
        target = params.get("target_skill","")
        task = params.get("task_description","Autonomous evolution cycle")
        session = self._new_session(target, task)
        orch = EvolutionOrchestrator(session, None)
        _orchestrators[session.workspace_id] = orch
        asyncio.create_task(self._bg_run(session.workspace_id, orch))
        return SkillResult(True, {"workspace_id": session.workspace_id, "state": session.to_dict()})

    async def _bg_run(self, ws_id, orch):
        try:
            async for _ in orch.run():
                pass
        except Exception as e:
            logger.error(f"BG orchestrator crashed: {e}")

    async def _action_status(self, params):
        from core.skill_registry import SkillResult
        ws_id = params.get("workspace_id") or (list(_sessions.keys())[-1] if _sessions else None)
        if ws_id and ws_id in _sessions:
            return SkillResult(True, {"state": _sessions[ws_id].to_dict()})
        return SkillResult(False, None, error="No active session")

    async def _action_approve(self, params):
        from core.skill_registry import SkillResult
        ws_id = params.get("workspace_id") or (list(_orchestrators.keys())[-1] if _orchestrators else None)
        orch = _orchestrators.get(ws_id) if ws_id else None
        if not orch:
            return SkillResult(False, None, error="No orchestrator found")
        orch.approve()
        s = _sessions.get(ws_id)
        if s:
            s.add_log("[PHASE-4] BLUEPRINT_APPROVED  by=human", "success")
        return SkillResult(True, {"approved": True, "workspace_id": ws_id, "state": s.to_dict() if s else None})

    async def _action_revise(self, params):
        from core.skill_registry import SkillResult
        ws_id = params.get("workspace_id") or (list(_sessions.keys())[-1] if _sessions else None)
        s = _sessions.get(ws_id) if ws_id else None
        if s:
            s.add_log("[PHASE-4] REVISE_REQUESTED — blueprint sent back for revision", "warn")
            s.approved = False
            return SkillResult(True, {"revised": True, "workspace_id": ws_id, "state": s.to_dict()})
        return SkillResult(False, None, error="No active session")

    async def _action_reset(self, params):
        from core.skill_registry import SkillResult
        ws_id = params.get("workspace_id") or (list(_orchestrators.keys())[-1] if _orchestrators else None)
        orch = _orchestrators.pop(ws_id, None) if ws_id else None
        if orch:
            orch.abort()
        _sessions.pop(ws_id, None)
        return SkillResult(True, {"reset": True, "workspace_id": ws_id})

    async def _action_get_logs(self, params):
        from core.skill_registry import SkillResult
        ws_id = params.get("workspace_id") or (list(_sessions.keys())[-1] if _sessions else None)
        s = _sessions.get(ws_id) if ws_id else None
        if s:
            return SkillResult(True, {"logs": [l.to_dict() for l in s.logs]})
        return SkillResult(False, None, error="No session found")


skill_instance = SelfEvolutionSkill()
