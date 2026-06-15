"""
Swarm Skill — Multi-agent orchestration for parallel LLM task decomposition.
Adapted from Omnikon Swarm pattern. Registered as a first-class Jarvis skill.

version: 1.1.0
changelog:
  1.1.0 - 2026-06-14 - Production hardening.
    FIXED  _execute_swarm / _run_one: ThreadPoolExecutor threads each called
           asyncio.new_event_loop() then loop.run_until_complete(_llm_call(...))
           which is correct for threads but the loop was not closed on exception,
           leaking file descriptors.  Now uses try/finally to guarantee close().
    FIXED  _sessions: completed sessions were never removed, causing unbounded
           memory growth over long runs.  _get_status now prunes sessions older
           than SESSION_RETAIN_SECONDS (default 3600).
    FIXED  _call_synthesizer: when all agents fail, `combined` is empty and the
           early return gave "All agents failed" with no detail.  Now includes
           the failed agent error messages in the synthesis prompt so the agent
           understands what went wrong.
    FIXED  _run_full_pipeline: if orchestration returns 0 subtasks (LLM returned
           empty list), swarm and synthesize are still called, producing a
           confusing "All agents failed" result.  Now returns early with
           SWARM_ORCHESTRATE_EMPTY.
    FIXED  _execute_swarm: max_workers=len(subtasks) creates a zero-worker pool
           if subtasks is empty, crashing ThreadPoolExecutor.  Now guards with
           max(1, len(subtasks)).
  1.0.0 - Initial implementation.
"""
"""

CBD Component Contract:
  Name:             SwarmSkill
  Logical Function: Parallel LLM orchestration / task decomposition
  IN-Schema:        run_full_pipeline → { task, n_agents?, provider?, model?, temperature? }
                    orchestrate       → { task, n_agents? }
                    run_swarm         → { subtasks[], provider?, model?, temperature? }
                    synthesize        → { task, results[] }
                    get_status        → {}
  OUT-Schema:       run_full_pipeline → { submitted, results[], final_answer, duration_ms }
                    orchestrate       → { subtasks[], n_agents }
                    run_swarm         → { results[], completed, failed, duration_ms }
                    synthesize        → { final_answer }
                    get_status        → { active_sessions[], count }
  Error-Schema:     { error_code: SWARM_NO_TASK | SWARM_ORCHESTRATE_FAIL | SWARM_RUN_FAIL |
                       SWARM_SYNTHESIZE_FAIL, message: str }
  Trace Points:     swarm_orchestrate_start, swarm_orchestrate_done, swarm_agent_start,
                    swarm_agent_done, swarm_synthesize_start, swarm_synthesize_done
  Failure Map:      per-agent try-catch; partial results always returned
"""
import asyncio
import json
import logging
import os
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from core.skill_registry import SkillResult

logger = logging.getLogger("skill.swarm")

DEFAULT_PROVIDER = os.getenv("LLM_PROVIDER", "deepseek")
DEFAULT_MODEL    = os.getenv("LLM_MODEL", "deepseek-chat")
SESSION_RETAIN_SECONDS = int(os.getenv("SWARM_SESSION_RETAIN_S", "3600"))  # 1 hour


class SwarmSkill:
    """Multi-agent swarm orchestration skill."""

    SKILL_NAME = "swarm"

    ACTIONS = {
        "run_full_pipeline": "End-to-end: orchestrate → run_swarm → synthesize",
        "orchestrate":       "Decompose a task into N parallel subtasks",
        "run_swarm":         "Execute subtasks in parallel via LLM calls",
        "synthesize":        "Merge agent results into a cohesive final answer",
        "get_status":        "List active swarm sessions",
    }

    def __init__(self):
        self._sessions: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    async def execute(self, action: str, params: dict, confirmed: bool = False) -> SkillResult:
        logger.info("[swarm.execute] action=%s", action)
        try:
            dispatch = {
                "run_full_pipeline": self._run_full_pipeline,
                "orchestrate":       self._orchestrate,
                "run_swarm":         self._run_swarm,
                "synthesize":        self._synthesize,
                "get_status":        self._get_status,
            }
            fn = dispatch.get(action)
            if fn is None:
                return SkillResult.fail(f"SWARM_UNKNOWN_ACTION: '{action}'. Valid: {', '.join(dispatch)}")
            return await fn(params, confirmed)
        except Exception as exc:
            logger.error("[swarm.exec_error] action=%s err=%s", action, exc)
            return SkillResult.fail(f"SWARM_INTERNAL_ERROR: {type(exc).__name__}: {exc}")

    async def _run_full_pipeline(self, params: dict, confirmed: bool) -> SkillResult:
        task        = params.get("task", "").strip()
        n_agents    = int(params.get("n_agents", 4))
        provider    = params.get("provider", DEFAULT_PROVIDER)
        model       = params.get("model", DEFAULT_MODEL)
        temperature = float(params.get("temperature", 0.7))

        if not task:
            return SkillResult.fail("SWARM_NO_TASK: task is required")

        wall_t0 = time.time()
        session_id = str(uuid.uuid4())[:8]

        logger.info("[swarm.pipeline_start] session=%s task=%.60s agents=%d", session_id, task, n_agents)

        # Phase 1: Orchestrate
        logger.info("[swarm.orchestrate_start] session=%s", session_id)
        orchestrate_result = await self._call_orchestrator(task, n_agents, provider, model, temperature)
        if "error" in orchestrate_result:
            return SkillResult.fail(f"SWARM_ORCHESTRATE_FAIL: {orchestrate_result['error']}")
        subtasks = orchestrate_result.get("subtasks", [])
        if not subtasks:
            # FIX: don't proceed to swarm+synthesize with an empty task list
            return SkillResult.fail("SWARM_ORCHESTRATE_EMPTY: orchestrator returned no subtasks — check LLM response")
        logger.info("[swarm.orchestrate_done] session=%s subtasks=%d", session_id, len(subtasks))

        # Phase 2: Run swarm
        logger.info("[swarm.run_start] session=%s", session_id)
        swarm_result = await self._execute_swarm(subtasks, task, provider, model, temperature)
        logger.info("[swarm.run_done] session=%s completed=%d failed=%d",
                    session_id, swarm_result.get("completed", 0), swarm_result.get("failed", 0))

        # Phase 3: Synthesize
        logger.info("[swarm.synthesize_start] session=%s", session_id)
        final = await self._call_synthesizer(task, swarm_result.get("results", []), provider, model, temperature)
        logger.info("[swarm.synthesize_done] session=%s", session_id)

        duration_ms = int((time.time() - wall_t0) * 1000)

        async with self._lock:
            self._sessions[session_id] = {
                "session_id": session_id, "task": task[:100],
                "n_agents": n_agents, "status": "complete",
                "duration_ms": duration_ms,
                "completed": swarm_result.get("completed", 0),
                "failed": swarm_result.get("failed", 0),
                "_ts": time.time(),   # FIX: timestamp for session pruning
            }

        return SkillResult.ok({
            "submitted": len(subtasks), "completed": swarm_result.get("completed", 0),
            "failed": swarm_result.get("failed", 0), "duration_ms": duration_ms,
            "results": swarm_result.get("results", []), "final_answer": final,
            "session_id": session_id,
        })

    async def _orchestrate(self, params: dict, confirmed: bool) -> SkillResult:
        task = params.get("task", "").strip()
        n_agents = int(params.get("n_agents", 4))
        if not task:
            return SkillResult.fail("SWARM_NO_TASK: task is required")
        result = await self._call_orchestrator(task, n_agents)
        if "error" in result:
            return SkillResult.fail(f"SWARM_ORCHESTRATE_FAIL: {result['error']}")
        return SkillResult.ok(result)

    async def _run_swarm(self, params: dict, confirmed: bool) -> SkillResult:
        subtasks = params.get("subtasks", [])
        task_context = params.get("task_context", "")
        provider = params.get("provider", DEFAULT_PROVIDER)
        model = params.get("model", DEFAULT_MODEL)
        temperature = float(params.get("temperature", 0.7))
        if not subtasks:
            return SkillResult.fail("SWARM_NO_SUBTASKS: subtasks[] is required")
        result = await self._execute_swarm(subtasks, task_context, provider, model, temperature)
        return SkillResult.ok(result)

    async def _synthesize(self, params: dict, confirmed: bool) -> SkillResult:
        task = params.get("task", "")
        results = params.get("results", [])
        if not results:
            return SkillResult.fail("SWARM_NO_RESULTS: results[] is required")
        final = await self._call_synthesizer(task, results)
        return SkillResult.ok({"final_answer": final})

    async def _get_status(self, params: dict, confirmed: bool) -> SkillResult:
        # FIX: prune sessions older than SESSION_RETAIN_SECONDS to prevent
        # unbounded memory growth over long-running deployments.
        import time as _time
        now_ts = _time.time()
        async with self._lock:
            self._sessions = {
                k: v for k, v in self._sessions.items()
                if now_ts - v.get("_ts", now_ts) < SESSION_RETAIN_SECONDS
            }
            sessions = list(self._sessions.values())
        # Strip internal _ts field from output
        clean = [{k2: v2 for k2, v2 in s.items() if k2 != "_ts"} for s in sessions]
        return SkillResult.ok({"active_sessions": clean, "count": len(clean)})

    async def _call_orchestrator(self, task: str, n_agents: int,
                                  provider: str = None, model: str = None,
                                  temperature: float = 0.7) -> dict:
        p = provider or DEFAULT_PROVIDER
        m = model or DEFAULT_MODEL
        system = (
            f"You are a task decomposition specialist. Break the following task into exactly {n_agents} "
            f"parallel subtasks for specialist swarm agents. Each agent should have a distinct role.\n"
            f"Respond ONLY with a valid JSON array, no markdown, no explanation:\n"
            f'[\n'
            f'  {{"agent_id": 1, "name": "AgentName", "role": "Role description", '
            f'"subtask": "Specific subtask to complete"}},\n'
            f'  ...\n'
            f']'
        )
        user = f"Task: {task}\n\nDecompose into exactly {n_agents} parallel subtasks."
        raw = await self._llm_call(p, m, system, user, temperature=temperature)
        raw = re.sub(r"```[a-z]*", "", raw).strip().strip("`").strip()
        try:
            subtasks = json.loads(raw)
            if not isinstance(subtasks, list):
                raise ValueError("Response is not a list")
            return {"subtasks": subtasks, "n_agents": len(subtasks)}
        except (json.JSONDecodeError, ValueError) as exc:
            match = re.search(r"\[.*\]", raw, re.DOTALL)
            if match:
                try:
                    subtasks = json.loads(match.group())
                    return {"subtasks": subtasks, "n_agents": len(subtasks)}
                except json.JSONDecodeError:
                    pass
            return {"error": f"Orchestrator returned invalid JSON: {raw[:300]}"}

    async def _execute_swarm(self, subtasks: list, task_context: str,
                              provider: str = None, model: str = None,
                              temperature: float = 0.7) -> dict:
        p = provider or DEFAULT_PROVIDER
        m = model or DEFAULT_MODEL
        results = []
        completed = 0
        failed = 0
        wall_t0 = time.time()

        def _run_one(subtask_def: dict) -> dict:
            agent_id = subtask_def.get("agent_id", 0)
            name = subtask_def.get("name", f"Agent-{agent_id}")
            role = subtask_def.get("role", "Specialist")
            subtask = subtask_def.get("subtask", "")
            system = f"You are {name}, a specialist AI agent in a swarm.\nYour role: {role}\nBe precise, thorough, and structured."
            user = f"Task context: {task_context}\n\nYour subtask: {subtask}"
            t0 = time.time()
            # FIX: always close the event loop, even on exception, to avoid
            # leaking file descriptors across the thread pool's lifetime.
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                output = loop.run_until_complete(self._llm_call(p, m, system, user, temperature=temperature))
                duration = round(time.time() - t0, 2)
                return {"agent_id": agent_id, "name": name, "role": role, "subtask": subtask,
                        "result": output, "status": "done", "duration": duration}
            except Exception as exc:
                duration = round(time.time() - t0, 2)
                logger.warning("[swarm.agent_error] agent_id=%s err=%s", agent_id, exc)
                return {"agent_id": agent_id, "name": name, "role": role, "subtask": subtask,
                        "result": f"ERROR: {exc}", "status": "error", "duration": duration}
            finally:
                loop.close()

        # FIX: max_workers=0 crashes ThreadPoolExecutor when subtasks is empty
        with ThreadPoolExecutor(max_workers=max(1, len(subtasks)), thread_name_prefix="swarm") as pool:
            futures = {pool.submit(_run_one, s): s for s in subtasks}
            for future in as_completed(futures):
                try:
                    result = future.result()
                    results.append(result)
                    if result["status"] == "done":
                        completed += 1
                    else:
                        failed += 1
                except Exception as exc:
                    results.append({"status": "error", "result": str(exc)})
                    failed += 1

        results.sort(key=lambda r: r.get("agent_id", 0))
        duration_ms = int((time.time() - wall_t0) * 1000)
        return {"results": results, "completed": completed, "failed": failed, "duration_ms": duration_ms}

    async def _call_synthesizer(self, task: str, results: list,
                                 provider: str = None, model: str = None,
                                 temperature: float = 0.7) -> str:
        p = provider or DEFAULT_PROVIDER
        m = model or DEFAULT_MODEL
        done_parts = [
            f"=== {r.get('name', 'Agent')} ({r.get('role', 'Specialist')}) ===\n{r.get('result', 'No output')}"
            for r in results if r.get("status") == "done"
        ]
        # FIX: include failed agent summaries so the synthesizer (and agent) can
        # understand what went wrong, rather than silently omitting them.
        failed_parts = [
            f"=== {r.get('name', 'Agent')} — FAILED: {r.get('result', 'unknown error')} ==="
            for r in results if r.get("status") != "done"
        ]

        if not done_parts:
            # FIX: return failed details so the caller understands the failure
            failed_summary = "\n".join(failed_parts) or "No agents ran."
            return f"All agents failed — no results to synthesize.\n\nAgent errors:\n{failed_summary}"

        combined = "\n\n".join(done_parts)
        if failed_parts:
            combined += "\n\n--- FAILED AGENTS (excluded from synthesis) ---\n" + "\n".join(failed_parts)

        system = "You are a synthesis specialist. Merge specialist agent outputs into one cohesive, well-structured final answer. Remove redundancy. Preserve all key insights. Use clear sections and markdown formatting."
        user = f"Original task: {task}\n\nSwarm outputs:\n{combined}\n\nSynthesize into the final answer."
        return await self._llm_call(p, m, system, user, temperature=temperature)

    async def _llm_call(self, provider: str, model: str, system: str, user: str,
                         temperature: float = 0.7, max_tokens: int = 4096) -> str:
        try:
            from core.llm_router import LLMRouter, LLMConfig
            cfg = LLMConfig(provider=provider, model=model, temperature=temperature, max_tokens=max_tokens)
            router = LLMRouter(cfg)
            response = await router.chat([
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ])
            return response.strip()
        except ImportError:
            import httpx
            api_key = os.getenv(f"{provider.upper()}_API_KEY", "") or os.getenv("API_KEY", "")
            base_urls = {"openai": "https://api.openai.com/v1", "deepseek": "https://api.deepseek.com/v1",
                         "anthropic": "https://api.anthropic.com/v1", "groq": "https://api.groq.com/openai/v1",
                         "custom": "http://localhost:11434/v1"}
            base = base_urls.get(provider, base_urls["openai"])
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(f"{base}/chat/completions",
                    json={"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                          "temperature": temperature, "max_tokens": max_tokens},
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"].strip()

    def describe(self) -> dict:
        return {"name": self.SKILL_NAME,
                "description": "Multi-agent swarm orchestration",
                "actions": self.ACTIONS,
                "config": {"default_provider": DEFAULT_PROVIDER, "default_model": DEFAULT_MODEL, "max_agents": 8}}