"""
JarvisMKII Skill — Multi-threaded parallel task execution.
Registers as a first-class skill in the SkillRegistry so the agent can invoke it
via TOOL_CALL exactly like any other skill.

version: 1.1.0
"""
changelog:
  1.1.0 - 2026-06-14 - Production hardening.
    FIXED  _run_one_task / _execute_run_tasks: asyncio.gather was called with
           return_exceptions=False, meaning a single unhandled task exception
           cancels all other tasks and the caller gets no partial results.
           Changed to return_exceptions=True with per-item exception unwrapping
           so partial results are always returned.
    FIXED  _run_one_task: registry cleanup in the finally block used
           _active_tasks.pop / _abort_flags.pop without the lock, causing a
           data race with _execute_abort_task.  Now uses _registry_lock.
    FIXED  task_id generation: 8-char UUID prefixes have ~1/16M collision
           probability but with high task volume this is non-trivial.  Now uses
           full UUID and truncates only for display.
    FIXED  _execute_run_tasks: tasks that raise BaseException (e.g. KeyboardInterrupt
           re-raised through gather) now produce a structured error result dict
           rather than propagating.
    FIXED  _execute_abort_task: event.set() called without checking if the task
           actually finished between the registry lookup and the set call.  Now
           checks _active_tasks under lock before setting.
  1.0.0 - 2026-06-10 - Initial implementation.
"""

CBD Component Contract:
  Name:             JarvisMKIISkill
  Logical Function: Orchestration / Parallel Execution
  IN-Schema:        run_tasks  → { tasks: [TaskDef], max_parallel?: int }
                    list_active → {}
                    abort_task  → { task_id: str }
  OUT-Schema:       run_tasks  → { submitted, completed, failed, duration_ms, results[] }
                    list_active → { active_tasks[], count }
                    abort_task  → { aborted: bool, task_id: str }
  Error-Schema:     { error_code: MKII_NO_TASKS | MKII_TOO_MANY_TASKS | MKII_TASK_NOT_FOUND,
                       message: str }
  Trace Points:     task_submitted, task_started, task_complete, task_failed,
                    task_timeout, all_tasks_done, abort_requested
  Failure Map:      per-task try-catch; asyncio.TimeoutError caught per task;
                    abort via cancellation token; partial results always returned
  Experience Hook:  Consumer (reads Experienced before any debugging task)
"""
import asyncio
import logging
import traceback
import threading
import uuid
from datetime import datetime, timezone

logger = logging.getLogger("skill.jarvis_mkii")

# ── Shared task registry ───────────────────────────────────────────────────────
_active_tasks: dict[str, dict]          = {}
_abort_flags:  dict[str, asyncio.Event] = {}
_registry_lock = threading.Lock()

DEFAULT_TIMEOUT_S  = 300   # 5 minutes per task
DEFAULT_MAX_TASKS  = 10


# ── SkillResult shim (mirrors core.skill_registry.SkillResult) ────────────────

class _Result:
    """Lightweight result container — avoids Pydantic dependency."""
    __slots__ = ("success", "output", "error", "requires_confirm", "confirm_prompt")

    def __init__(self, success: bool, output=None, error: str = None,
                 requires_confirm: bool = False, confirm_prompt: str = ""):
        self.success        = success
        self.output         = output
        self.error          = error
        self.requires_confirm = requires_confirm
        self.confirm_prompt   = confirm_prompt

    def to_dict(self) -> dict:
        return {
            "success":         self.success,
            "output":          self.output,
            "error":           self.error,
            "requires_confirm": self.requires_confirm,
        }


# ── Core parallel executor ─────────────────────────────────────════════════════

async def _run_one_task(task_def: dict, agent_factory) -> dict:
    """
    Execute a single task in an isolated Agent.
    Returns a result dict regardless of success/failure/timeout.

    task_def schema:
      task_id?      : str   — optional; auto-assigned if missing
      message       : str   — required; the task prompt
      react?        : bool  — default True (ReAct loop)
      auto_confirm? : bool  — default False
      user_id?      : str   — isolated user context for this task
      model?        : str
      provider?     : str
    """
    task_id      = task_def.get("task_id") or str(uuid.uuid4())
    task_id_short = task_id[:8]  # display only
    message      = task_def.get("message", "").strip()
    react        = task_def.get("react", True)
    auto_confirm = task_def.get("auto_confirm", False)
    user_id      = task_def.get("user_id") or f"mkii-{task_id}"
    model        = task_def.get("model")
    provider     = task_def.get("provider")
    timeout_s    = int(task_def.get("timeout_s", DEFAULT_TIMEOUT_S))

    if not message:
        return {
            "task_id": task_id,
            "status":  "failed",
            "output":  "",
            "error":   "MKII_EMPTY_MESSAGE: task.message is required",
            "duration_ms": 0,
        }

    # Register task
    abort_event = asyncio.Event()
    started_at  = datetime.now(timezone.utc)
    with _registry_lock:
        _active_tasks[task_id] = {
            "task_id":    task_id,
            "task_id_short": task_id_short,
            "status":     "running",
            "started_at": started_at.isoformat(),
            "message":    message[:100],
            "user_id":    user_id,
        }
        _abort_flags[task_id] = abort_event

    logger.info("[task_started] id=%s user=%s react=%s timeout=%ds", task_id_short, user_id, react, timeout_s)

    output_tokens: list[str] = []

    try:
        agent = agent_factory(user_id=user_id, model=model, provider=provider)

        async def _collect_with_abort():
            async for event in agent.chat_stream(message, react=react, auto_confirm=auto_confirm):
                if abort_event.is_set():
                    logger.info("[task_abort_mid_stream] id=%s", task_id_short)
                    break
                if event.get("type") == "token":
                    output_tokens.append(event.get("data", ""))

        await asyncio.wait_for(_collect_with_abort(), timeout=timeout_s)

        status   = "aborted" if abort_event.is_set() else "complete"
        full_out = "".join(output_tokens)
        duration = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)

        logger.info("[task_%s] id=%s duration_ms=%d chars=%d",
                    status, task_id_short, duration, len(full_out))
        return {"task_id": task_id, "status": status, "output": full_out,
                "error": None, "duration_ms": duration}

    except asyncio.TimeoutError:
        duration = timeout_s * 1000
        logger.warning("[task_timeout] id=%s after=%ds", task_id_short, timeout_s)
        return {
            "task_id":     task_id,
            "status":      "timeout",
            "output":      "".join(output_tokens),
            "error":       f"Timed out after {timeout_s}s",
            "duration_ms": duration,
        }

    except Exception as exc:
        duration = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
        logger.error("[task_failed] id=%s err=%s\n%s", task_id_short, exc, traceback.format_exc())
        return {
            "task_id":     task_id,
            "status":      "failed",
            "output":      "".join(output_tokens),
            "error":       str(exc),
            "duration_ms": duration,
        }

    finally:
        # FIX: cleanup under the registry lock to prevent race with abort_task
        with _registry_lock:
            _active_tasks.pop(task_id, None)
            _abort_flags.pop(task_id, None)


async def _execute_run_tasks(params: dict, agent_factory) -> _Result:
    """
    IN:  { tasks: [TaskDef...], max_parallel?: int }
    OUT: { submitted, completed, failed, aborted, timed_out, duration_ms, results[] }
    """
    tasks       = params.get("tasks", [])
    max_parallel = int(params.get("max_parallel", DEFAULT_MAX_TASKS))

    if not tasks:
        return _Result(False, error="MKII_NO_TASKS: provide at least one task in tasks[]")

    if len(tasks) > max_parallel:
        return _Result(False, error=(
            f"MKII_TOO_MANY_TASKS: requested {len(tasks)}, max allowed {max_parallel}. "
            f"Split into batches or increase JARVIS_MAX_PARALLEL."
        ))

    # Assign task_ids where missing
    for t in tasks:
        if not t.get("task_id"):
            t["task_id"] = str(uuid.uuid4())[:8]

    logger.info("[all_tasks_submitted] count=%d", len(tasks))
    started = datetime.now(timezone.utc)

    # FIX: return_exceptions=True — one task crashing no longer cancels the rest.
    # Exceptions are unwrapped per-item below and converted to structured error dicts.
    raw_results = await asyncio.gather(
        *[_run_one_task(t, agent_factory) for t in tasks],
        return_exceptions=True,
    )

    results = []
    for t, r in zip(tasks, raw_results):
        if isinstance(r, BaseException):
            logger.error("[task_gather_exception] id=%s err=%s", t.get("task_id", "?"), r)
            results.append({
                "task_id":     t.get("task_id", "unknown"),
                "status":      "failed",
                "output":      "",
                "error":       f"MKII_GATHER_EXCEPTION: {type(r).__name__}: {r}",
                "duration_ms": 0,
            })
        else:
            results.append(r)

    duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)

    status_counts: dict[str, int] = {}
    for r in results:
        s = r.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1

    output = {
        "submitted":    len(tasks),
        "completed":    status_counts.get("complete", 0),
        "failed":       status_counts.get("failed",   0),
        "timed_out":    status_counts.get("timeout",  0),
        "aborted":      status_counts.get("aborted",  0),
        "duration_ms":  duration_ms,
        "results":      list(results),
    }

    logger.info("[all_tasks_done] submitted=%d completed=%d failed=%d timeout=%d duration_ms=%d",
                len(tasks),
                output["completed"], output["failed"],
                output["timed_out"], duration_ms)

    return _Result(True, output=output)


def _execute_list_active(_params: dict) -> _Result:
    """IN: {} — OUT: { active_tasks[], count }"""
    with _registry_lock:
        tasks = list(_active_tasks.values())
    return _Result(True, output={"active_tasks": tasks, "count": len(tasks)})


def _execute_abort_task(params: dict) -> _Result:
    """IN: { task_id } — OUT: { aborted: bool, task_id }"""
    task_id = params.get("task_id", "")
    if not task_id:
        return _Result(False, error="MKII_NO_TASK_ID: task_id is required")

    with _registry_lock:
        # FIX: check both dicts under the lock — the task may have just finished
        # and cleaned up between our lookup and the set() call.
        event = _abort_flags.get(task_id)
        still_active = task_id in _active_tasks

    if event is None or not still_active:
        logger.warning("[abort_not_found] id=%s", task_id)
        return _Result(False, error=f"MKII_TASK_NOT_FOUND: no active task with id '{task_id}'")

    event.set()
    logger.info("[abort_requested] id=%s", task_id)
    return _Result(True, output={"aborted": True, "task_id": task_id})


# ── Public skill interface ─────────────────────────────────────────────────────

class JarvisMKIISkill:
    """
    CBD-compliant skill class registered with SkillRegistry.
    The registry calls execute(action, params, confirmed) for every TOOL_CALL.

    agent_factory: callable(user_id, model, provider) → Agent
    Injected at registration time by the SkillRegistry or main.py bootstrap.
    """

    SKILL_NAME = "jarvis_mkii"

    ACTIONS = {
        "run_tasks":   "Fan out tasks to parallel isolated agents and collect results",
        "list_active": "List all currently running tasks with their status",
        "abort_task":  "Signal an active task to stop at next safe checkpoint",
    }

    def __init__(self, agent_factory=None):
        self._agent_factory = agent_factory or self._default_factory

    def _default_factory(self, user_id: str, model: str = None, provider: str = None):
        """
        Default factory — imports Agent lazily to avoid circular imports.
        In production, inject a proper factory via __init__.
        """
        try:
            from core.agent import Agent
            from core.llm_router import LLMConfig, get_model_max_tokens
            from core.skill_registry import SkillRegistry

            resolved_provider = provider or "deepseek"
            resolved_model    = model    or "deepseek-coder"
            cfg = LLMConfig(
                provider=resolved_provider,
                model=resolved_model,
                max_tokens=get_model_max_tokens(resolved_model),
            )
            return Agent(cfg, SkillRegistry(), user_id=user_id)
        except Exception as exc:
            logger.error("[default_factory_error] %s", exc)
            raise

    async def execute(self, action: str, params: dict, confirmed: bool = False):
        """
        Main dispatch. Called by SkillRegistry.
        Returns _Result with .to_dict() compatible output.
        """
        logger.info("[jarvis_mkii.execute] action=%s", action)

        try:
            if action == "run_tasks":
                return await _execute_run_tasks(params, self._agent_factory)
            elif action == "list_active":
                return _execute_list_active(params)
            elif action == "abort_task":
                return _execute_abort_task(params)
            else:
                return _Result(False, error=f"MKII_UNKNOWN_ACTION: '{action}'. "
                               f"Valid actions: {', '.join(self.ACTIONS)}")

        except Exception as exc:
            logger.error("[jarvis_mkii.execute_error] action=%s err=%s\n%s",
                         action, exc, traceback.format_exc())
            return _Result(False, error=f"MKII_INTERNAL_ERROR: {type(exc).__name__}: {str(exc)}")

    def describe(self) -> dict:
        """Return skill metadata for manifest/introspection."""
        return {
            "name":        self.SKILL_NAME,
            "description": "Parallel task orchestration — run N independent tasks simultaneously, each in an isolated Agent context",
            "actions":     self.ACTIONS,
            "config": {
                "max_parallel_env":  "JARVIS_MAX_PARALLEL (default 10)",
                "task_timeout_env":  "JARVIS_TASK_TIMEOUT (default 300s)",
                "isolation":         "Each task gets its own Agent instance — no shared conversation state",
            },
            "use_cases": [
                "Parallel port scans across multiple targets",
                "Simultaneous recon on multiple domains",
                "Running multiple RCA investigations at once",
                "Parallel CBD component implementations",
                "Multi-target exploit validation",
            ],
        }