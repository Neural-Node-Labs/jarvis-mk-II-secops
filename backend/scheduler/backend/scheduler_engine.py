#!/usr/bin/env python3
"""
scheduler_engine.py — Task Scheduling Skill v1.0.0

Background daemon thread that polls TaskStore every N seconds for due tasks.
Dispatches to TaskExecutor and updates task status accordingly.

CHANGELOG:
  v1.0.0 — Initial implementation
"""

import os
import threading
import time
import logging
from datetime import datetime, timezone
from typing import Optional

from .task_store import TaskStore, STATUS_ACTIVE, STATUS_RUNNING, \
    STATUS_COMPLETED, STATUS_FAILED
from .task_executor import TaskExecutor
from .cron_parser import CronParser

logger = logging.getLogger("scheduler_engine")

POLL_INTERVAL = int(os.environ.get("SCHEDULER_POLL_INTERVAL", "10"))


class SchedulerEngine:
    """Background scheduler daemon."""

    def __init__(self, task_store: TaskStore, poll_interval: int = POLL_INTERVAL):
        self.task_store = task_store
        self.executor = TaskExecutor()
        self.cron_parser = CronParser()
        self.poll_interval = poll_interval
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False
        self._started_at: Optional[int] = None

    def start(self):
        """Start the scheduler background thread."""
        if self._running:
            logger.warning("Scheduler already running")
            return

        self._running = True
        self._stop_event.clear()
        self._started_at = int(datetime.now(timezone.utc).timestamp())
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info(f"Scheduler started (poll interval: {self.poll_interval}s)")

    def stop(self):
        """Stop the scheduler background thread."""
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("Scheduler stopped")

    @property
    def is_running(self) -> bool:
        return self._running and self._thread and self._thread.is_alive()

    @property
    def uptime_seconds(self) -> int:
        if self._started_at is None:
            return 0
        return int(datetime.now(timezone.utc).timestamp()) - self._started_at

    def _run_loop(self):
        """Main scheduler loop."""
        while not self._stop_event.is_set():
            try:
                self._process_due_tasks()
            except Exception as e:
                logger.error(f"Error in scheduler loop: {e}")

            self._stop_event.wait(self.poll_interval)

    def _process_due_tasks(self):
        """Find and execute all due tasks."""
        due_tasks = self.task_store.get_due_tasks()

        for task in due_tasks:
            self._execute_task(task)

    def _execute_task(self, task: dict):
        """Execute a single due task."""
        task_id = task["id"]
        now = int(datetime.now(timezone.utc).timestamp())

        logger.info(f"Executing task {task_id}: {task.get('name', 'unnamed')}")

        # Mark as running
        self.task_store.update_task(task_id, {
            "status": STATUS_RUNNING,
            "last_run_at": now,
        })

        # Add history entry (started)
        self.task_store.add_history(
            task_id=task_id,
            status=STATUS_RUNNING,
            started_at=now
        )

        # Execute
        result = self.executor.execute(task)

        # Determine new status
        if result.get("success", False):
            new_status = STATUS_COMPLETED
        else:
            new_status = STATUS_FAILED

        # Update task
        updates = {
            "status": STATUS_ACTIVE if task["type"] == "cron" else new_status,
            "run_count": task.get("run_count", 0) + 1,
        }

        # Compute next run for recurring tasks
        if task["type"] == "cron" and task.get("schedule_expr"):
            next_run = self.cron_parser.get_next_run(task["schedule_expr"])
            if next_run:
                updates["next_run_at"] = int(next_run.timestamp())
            else:
                updates["status"] = STATUS_FAILED
                updates["next_run_at"] = None
        elif task["type"] == "oneshot":
            updates["next_run_at"] = None

        # Check max_runs
        if task.get("max_runs", -1) > 0 and updates.get("run_count", 0) >= task["max_runs"]:
            updates["status"] = STATUS_COMPLETED

        self.task_store.update_task(task_id, updates)

        # Update history entry (finished)
        self.task_store.add_history(
            task_id=task_id,
            status=new_status,
            output=result.get("stdout", ""),
            error=result.get("stderr", ""),
            started_at=now,
            finished_at=int(datetime.now(timezone.utc).timestamp())
        )

        logger.info(f"Task {task_id} completed with status {new_status}")

    def get_status(self) -> dict:
        """Get scheduler status."""
        stats = self.task_store.get_stats()
        return {
            "running": self.is_running,
            "uptime_seconds": self.uptime_seconds,
            "poll_interval": self.poll_interval,
            "tasks_pending": stats.get("active", 0),
            "tasks_running": stats.get("running", 0),
            "total_tasks": stats.get("total", 0),
            "completed": stats.get("completed", 0),
            "failed": stats.get("failed", 0),
        }
