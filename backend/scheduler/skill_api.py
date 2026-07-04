#!/usr/bin/env python3
"""
skill_api.py — Task Scheduling Skill v1.0.0

Exposed API actions for scheduling and managing tasks.
Called both by Jarvis internally and by the web UI via HTTP.

CHANGELOG:
  v1.0.0 — Initial implementation
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from .task_store import TaskStore, STATUS_ACTIVE, STATUS_PAUSED, \
    STATUS_CANCELLED, STATUS_PENDING, VALID_STATUSES, VALID_TYPES
from .cron_parser import CronParser
from .scheduler_engine import SchedulerEngine

logger = logging.getLogger("skill_api")


class SkillAPI:
    """Task scheduling skill API."""

    def __init__(self, task_store: TaskStore, scheduler: SchedulerEngine):
        self.store = task_store
        self.scheduler = scheduler
        self.cron_parser = CronParser()

    def schedule(self, name: str, type: str = "oneshot",
                 schedule_expr: Optional[str] = None,
                 command: Optional[str] = None,
                 action: Optional[dict] = None,
                 max_runs: int = -1,
                 metadata: Optional[dict] = None) -> dict:
        """
        Schedule a new task.

        Args:
            name: Human-readable task name
            type: Task type - cron, oneshot, or queue
            schedule_expr: Cron expression or @shortcut (required for cron type)
            command: Shell command to execute
            action: Jarvis action dict {skill, action, params}
            max_runs: Max executions (-1 for unlimited)
            metadata: Additional metadata dict

        Returns:
            dict with task_id, status, next_run_at
        """
        # Validate type
        if type not in VALID_TYPES:
            return {
                "success": False,
                "error": f"Invalid type '{type}'. Must be one of: {', '.join(VALID_TYPES)}"
            }

        # Validate cron expression if provided
        next_run_at = None
        if schedule_expr:
            is_valid, msg = self.cron_parser.validate(schedule_expr)
            if not is_valid:
                return {"success": False, "error": msg}

            # Compute initial next run
            next_dt = self.cron_parser.get_next_run(schedule_expr)
            if next_dt:
                next_run_at = int(next_dt.timestamp())

        # For oneshot with no schedule_expr, set next_run_at to now
        if type == "oneshot" and not schedule_expr:
            next_run_at = int(datetime.now(timezone.utc).timestamp())

        # Validate that at least one of command or action is provided
        if not command and not action:
            return {
                "success": False,
                "error": "Either 'command' or 'action' must be provided"
            }

        task_data = {
            "name": name,
            "type": type,
            "schedule_expr": schedule_expr,
            "command": command,
            "action": action or {},
            "max_runs": max_runs,
            "next_run_at": next_run_at,
            "metadata": metadata or {},
        }

        task = self.store.create_task(task_data)

        return {
            "success": True,
            "task_id": task["id"],
            "status": task["status"],
            "next_run_at": task.get("next_run_at"),
            "task": task,
        }

    def list_tasks(self, status_filter: Optional[str] = None) -> dict:
        """
        List all tasks, optionally filtered by status.

        Args:
            status_filter: Optional status to filter by

        Returns:
            dict with tasks array
        """
        tasks = self.store.list_tasks(status_filter)
        return {
            "success": True,
            "count": len(tasks),
            "tasks": tasks,
        }

    def cancel_task(self, task_id: str) -> dict:
        """
        Cancel a task.

        Args:
            task_id: ID of task to cancel

        Returns:
            dict with status of operation
        """
        task = self.store.get_task(task_id)
        if not task:
            return {"success": False, "error": f"Task {task_id} not found"}

        if task["status"] in [STATUS_CANCELLED, STATUS_COMPLETED]:
            return {
                "success": True,
                "status": task["status"],
                "message": f"Task already in '{task['status']}' state"
            }

        updated = self.store.update_task(task_id, {"status": STATUS_CANCELLED})
        return {
            "success": True,
            "status": updated["status"],
            "task": updated,
        }

    def pause_task(self, task_id: str) -> dict:
        """
        Pause a task (stop scheduling until resumed).

        Args:
            task_id: ID of task to pause

        Returns:
            dict with status of operation
        """
        task = self.store.get_task(task_id)
        if not task:
            return {"success": False, "error": f"Task {task_id} not found"}

        if task["status"] != STATUS_ACTIVE:
            return {
                "success": False,
                "error": f"Cannot pause task in '{task['status']}' state"
            }

        updated = self.store.update_task(task_id, {"status": STATUS_PAUSED})
        return {
            "success": True,
            "status": updated["status"],
            "task": updated,
        }

    def resume_task(self, task_id: str) -> dict:
        """
        Resume a paused task.

        Args:
            task_id: ID of task to resume

        Returns:
            dict with status of operation
        """
        task = self.store.get_task(task_id)
        if not task:
            return {"success": False, "error": f"Task {task_id} not found"}

        if task["status"] != STATUS_PAUSED:
            return {
                "success": False,
                "error": f"Cannot resume task in '{task['status']}' state"
            }

        # Recompute next_run_at
        next_run_at = task.get("next_run_at")
        if task.get("schedule_expr") and not next_run_at:
            next_dt = self.cron_parser.get_next_run(task["schedule_expr"])
            if next_dt:
                next_run_at = int(next_dt.timestamp())

        updates = {"status": STATUS_ACTIVE}
        if next_run_at:
            updates["next_run_at"] = next_run_at

        updated = self.store.update_task(task_id, updates)
        return {
            "success": True,
            "status": updated["status"],
            "task": updated,
        }

    def get_task_history(self, task_id: str, limit: int = 50) -> dict:
        """
        Get execution history for a task.

        Args:
            task_id: ID of task
            limit: Max history entries to return

        Returns:
            dict with history array
        """
        task = self.store.get_task(task_id)
        if not task:
            return {"success": False, "error": f"Task {task_id} not found"}

        history = self.store.get_history(task_id, limit)
        return {
            "success": True,
            "count": len(history),
            "history": history,
        }

    def get_scheduler_status(self) -> dict:
        """
        Get scheduler status and statistics.

        Returns:
            dict with running, uptime, task counts
        """
        status = self.scheduler.get_status()
        status["success"] = True
        return status

    def get_task(self, task_id: str) -> dict:
        """
        Get a single task by ID.

        Args:
            task_id: ID of task

        Returns:
            dict with task data
        """
        task = self.store.get_task(task_id)
        if not task:
            return {"success": False, "error": f"Task {task_id} not found"}
        return {"success": True, "task": task}

    def delete_task(self, task_id: str) -> dict:
        """
        Delete a task permanently.

        Args:
            task_id: ID of task to delete

        Returns:
            dict with status
        """
        deleted = self.store.delete_task(task_id)
        if not deleted:
            return {"success": False, "error": f"Task {task_id} not found"}
        return {"success": True, "message": f"Task {task_id} deleted"}

    def get_stats(self) -> dict:
        """
        Get overall statistics.

        Returns:
            dict with stats
        """
        stats = self.store.get_stats()
        stats["success"] = True
        stats["history_count"] = self.store.get_total_history_count()
        stats["scheduler_running"] = self.scheduler.is_running
        stats["scheduler_uptime"] = self.scheduler.uptime_seconds
        return stats
