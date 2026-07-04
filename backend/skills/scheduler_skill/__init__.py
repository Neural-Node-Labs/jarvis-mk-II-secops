#!/usr/bin/env python3
"""
skills/scheduler_skill/__init__.py — Scheduler Skill (registry adapter) v1.0.0

This is the module SkillRegistry._load_scheduler() imports:

    from skills.scheduler_skill import SchedulerSkill
    return SchedulerSkill()

WHY THIS FILE EXISTS
---------------------
The task_scheduler package (TaskStore, CronParser, SchedulerEngine,
TaskExecutor, SkillAPI, server.py, main.py) was built as a fully standalone
service — its own SQLite store, its own background thread, its own HTTP
server on TASK_SCHEDULER_PORT. None of that is a "skill": SkillRegistry
requires a class named *Skill with an `execute(action, params, confirmed)`
coroutine, and no such class existed anywhere in the package, which is why
every call came back SKILL_NOT_FOUND: 'scheduler'.

This adapter is the missing piece. It reuses TaskStore + CronParser
directly (both are dependency-free and fine to embed), and exposes exactly
the action surface main.py's FastAPI routes and background loop already
call it with: list_tasks, create_task, get_task, update_task, delete_task,
toggle_task, get_due_tasks, list_runs, record_run.

WHAT WAS DELIBERATELY LEFT OUT
--------------------------------
SchedulerEngine (background polling thread), TaskExecutor (shell/queue
dispatch), skill_api.py's SkillAPI, and server.py's HTTP server are NOT
wired in here. main.py already runs its own asyncio `_scheduler_loop()`
that polls get_due_tasks and drives execution using the live Agent
sessions (needed for AI-call style tasks, not just shell commands) — if
SchedulerEngine's thread were also started against the same tasks.db, due
tasks would be picked up and executed twice, by two independent pollers.
If you actually want the standalone HTTP dashboard/server on port 9090 as
a *separate* process, that's fine — just point it at a different
TASK_SCHEDULER_DB, or don't run main.py's asyncio loop in that process.

CHANGELOG:
  v1.0.0 - Initial adapter: wires TaskStore + CronParser into a SkillResult-
           returning execute() dispatcher matching main.py's call sites.
"""
import os
import time
import logging

from core.skill_registry import SkillResult

from .task_store import (
    TaskStore,
    STATUS_ACTIVE, STATUS_PAUSED, STATUS_COMPLETED, STATUS_FAILED,
    TYPE_CRON, TYPE_ONESHOT, VALID_TYPES,
)
from .cron_parser import CronParser

logger = logging.getLogger("scheduler_skill")

DEFAULT_DB_PATH = os.environ.get(
    "JARVIS_SCHEDULER_DB",
    os.environ.get("TASK_SCHEDULER_DB", "/app/workspace/scheduler/tasks.db"),
)


class SchedulerSkill:
    """Cron-style recurring tasks, one-shot delayed execution, and Jarvis
    action triggers — persistent SQLite-backed storage (TaskStore) with
    cron parsing (CronParser). Due-task *execution* is driven by main.py's
    background loop; this skill only owns task/run storage, same pattern
    as the other skills in this registry."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self.store = TaskStore(self.db_path)
        self.cron = CronParser()

    async def execute(self, action: str, params: dict, confirmed: bool = False) -> SkillResult:
        params = params or {}
        handler = getattr(self, f"_action_{action}", None)
        if handler is None:
            return SkillResult.fail(
                f"UNKNOWN_ACTION: 'scheduler.{action}'. Available: list_tasks, create_task, "
                f"get_task, update_task, delete_task, toggle_task, get_due_tasks, list_runs, record_run"
            )
        try:
            return handler(params)
        except Exception as exc:
            logger.error("[scheduler_skill_error] action=%s err=%s", action, exc)
            return SkillResult.fail(f"scheduler.{action} failed: {type(exc).__name__}: {exc}")

    # ── CRUD ─────────────────────────────────────────────────────────────

    def _action_list_tasks(self, params: dict) -> SkillResult:
        status_filter = params.get("status")
        tasks = self.store.list_tasks(status_filter)
        user_id = params.get("user_id")
        if user_id:
            tasks = [
                t for t in tasks
                if t.get("created_by") == user_id
                or (t.get("metadata") or {}).get("user_id") == user_id
            ]
        return SkillResult.ok({"success": True, "count": len(tasks), "tasks": tasks})

    def _action_create_task(self, params: dict) -> SkillResult:
        name = (params.get("name") or "").strip()
        if not name:
            return SkillResult.fail("name is required")

        task_type = params.get("type", TYPE_ONESHOT)
        if task_type not in VALID_TYPES:
            return SkillResult.fail(f"Invalid type '{task_type}'. Must be one of: {', '.join(sorted(VALID_TYPES))}")

        schedule_expr = params.get("schedule_expr")
        next_run_at = params.get("next_run_at")
        if schedule_expr:
            ok, msg = self.cron.validate(schedule_expr)
            if not ok:
                return SkillResult.fail(msg)
            next_dt = self.cron.get_next_run(schedule_expr)
            if next_dt:
                next_run_at = int(next_dt.timestamp())
        elif task_type == TYPE_ONESHOT and not next_run_at:
            next_run_at = int(time.time())

        command = params.get("command")
        action_payload = params.get("action")
        if not command and not action_payload:
            return SkillResult.fail("Either 'command' or 'action' must be provided")

        metadata = dict(params.get("metadata") or {})
        # Preserve extra caller-provided hints (user_id, an execution-kind
        # tag like "ai_call" vs "command", etc.) inside metadata so the
        # main.py runner can branch on them without a schema change here.
        for extra_key in ("user_id", "task_type"):
            if params.get(extra_key) and extra_key not in metadata:
                metadata[extra_key] = params[extra_key]

        task = self.store.create_task({
            "name": name,
            "type": task_type,
            "schedule_expr": schedule_expr,
            "command": command,
            "action": action_payload or {},
            "max_runs": params.get("max_runs", -1),
            "next_run_at": next_run_at,
            "metadata": metadata,
            "created_by": params.get("user_id") or params.get("created_by") or "jarvis",
        })
        return SkillResult.ok({
            "success": True, "task_id": task["id"], "status": task["status"],
            "next_run_at": task.get("next_run_at"), "task": task,
        })

    def _action_get_task(self, params: dict) -> SkillResult:
        task_id = params.get("task_id")
        if not task_id:
            return SkillResult.fail("task_id is required")
        task = self.store.get_task(task_id)
        if not task:
            return SkillResult.fail(f"Task {task_id} not found")
        return SkillResult.ok({"success": True, "task": task})

    def _action_update_task(self, params: dict) -> SkillResult:
        task_id = params.get("task_id")
        if not task_id:
            return SkillResult.fail("task_id is required")
        if not self.store.get_task(task_id):
            return SkillResult.fail(f"Task {task_id} not found")

        updates = {k: v for k, v in params.items() if k != "task_id"}

        if updates.get("schedule_expr"):
            ok, msg = self.cron.validate(updates["schedule_expr"])
            if not ok:
                return SkillResult.fail(msg)
            next_dt = self.cron.get_next_run(updates["schedule_expr"])
            if next_dt:
                updates.setdefault("next_run_at", int(next_dt.timestamp()))

        task = self.store.update_task(task_id, updates)
        return SkillResult.ok({"success": True, "task": task})

    def _action_delete_task(self, params: dict) -> SkillResult:
        task_id = params.get("task_id")
        if not task_id:
            return SkillResult.fail("task_id is required")
        deleted = self.store.delete_task(task_id)
        if not deleted:
            return SkillResult.fail(f"Task {task_id} not found")
        return SkillResult.ok({"success": True, "message": f"Task {task_id} deleted"})

    def _action_toggle_task(self, params: dict) -> SkillResult:
        task_id = params.get("task_id")
        if not task_id:
            return SkillResult.fail("task_id is required")
        task = self.store.get_task(task_id)
        if not task:
            return SkillResult.fail(f"Task {task_id} not found")

        enabled = params.get("enabled")
        if enabled is None:
            enabled = task["status"] != STATUS_ACTIVE  # no explicit value given -> flip it

        updates = {}
        if enabled:
            next_run_at = task.get("next_run_at")
            if task.get("schedule_expr") and not next_run_at:
                next_dt = self.cron.get_next_run(task["schedule_expr"])
                if next_dt:
                    next_run_at = int(next_dt.timestamp())
            updates["status"] = STATUS_ACTIVE
            if next_run_at:
                updates["next_run_at"] = next_run_at
        else:
            updates["status"] = STATUS_PAUSED

        task = self.store.update_task(task_id, updates)
        return SkillResult.ok({"success": True, "status": task["status"], "task": task})

    # ── Due-task polling + run recording ────────────────────────────────

    def _action_get_due_tasks(self, params: dict) -> SkillResult:
        due = self.store.get_due_tasks()
        return SkillResult.ok({"success": True, "count": len(due), "tasks": due})

    def _action_list_runs(self, params: dict) -> SkillResult:
        task_id = params.get("task_id")
        if not task_id:
            return SkillResult.fail("task_id is required")
        if not self.store.get_task(task_id):
            return SkillResult.fail(f"Task {task_id} not found")
        limit = int(params.get("limit", 50))
        history = self.store.get_history(task_id, limit)
        return SkillResult.ok({"success": True, "count": len(history), "runs": history})

    def _action_record_run(self, params: dict) -> SkillResult:
        """Called after main.py's loop has actually run a due task (shell
        command or AI/action dispatch) — persists the result, advances
        run_count, recomputes next_run_at for cron tasks, and appends a
        history row. Accepts either {"status": "success"/"failed"} or a
        {"success": bool} shape for the outcome, since that's the one part
        of this contract I couldn't verify byte-for-byte against your
        FastAPI main.py (see note below the code)."""
        task_id = params.get("task_id")
        if not task_id:
            return SkillResult.fail("task_id is required")
        task = self.store.get_task(task_id)
        if not task:
            return SkillResult.fail(f"Task {task_id} not found")

        raw_status = params.get("status")
        if raw_status in ("success", "completed"):
            succeeded = True
        elif raw_status in ("failed", "error"):
            succeeded = False
        else:
            succeeded = bool(params.get("success", True))

        output = str(params.get("output", ""))[:20000]
        error = str(params.get("error", ""))[:5000]
        now = int(time.time())
        run_count = task.get("run_count", 0) + 1

        updates = {"run_count": run_count, "last_run_at": now}
        new_status = STATUS_ACTIVE if task["type"] == TYPE_CRON else (STATUS_COMPLETED if succeeded else STATUS_FAILED)

        if task["type"] == TYPE_CRON and task.get("schedule_expr"):
            next_dt = self.cron.get_next_run(task["schedule_expr"])
            if next_dt:
                updates["next_run_at"] = int(next_dt.timestamp())
            else:
                new_status = STATUS_FAILED
                updates["next_run_at"] = None
        elif task["type"] == TYPE_ONESHOT:
            updates["next_run_at"] = None

        if task.get("max_runs", -1) > 0 and run_count >= task["max_runs"]:
            new_status = STATUS_COMPLETED

        updates["status"] = new_status
        updated = self.store.update_task(task_id, updates)

        self.store.add_history(
            task_id=task_id,
            status=STATUS_COMPLETED if succeeded else STATUS_FAILED,
            output=output,
            error=error,
            started_at=params.get("started_at", now),
            finished_at=params.get("finished_at", now),
        )
        return SkillResult.ok({"success": True, "status": updated["status"], "task": updated})
