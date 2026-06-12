#!/usr/bin/env python3
"""
task_executor.py — Task Scheduling Skill v1.0.0

Dispatches task execution. Two modes:
1. shell — runs command via subprocess, captures stdout/stderr/returncode
2. jarvis_action — writes a TOOL_CALL JSON to a queue file for Jarvis to pick up

CHANGELOG:
  v1.0.0 — Initial implementation
"""

import subprocess
import json
import os
import time
from datetime import datetime, timezone
from typing import Optional

JARVIS_QUEUE_DIR = os.environ.get(
    "JARVIS_QUEUE_DIR",
    "/tmp/workspace/scheduler/queue"
)


class TaskExecutor:
    """Execute tasks via shell commands or Jarvis action dispatch."""

    def __init__(self, queue_dir: str = JARVIS_QUEUE_DIR):
        self.queue_dir = queue_dir
        os.makedirs(self.queue_dir, exist_ok=True)

    def execute_shell(self, command: str, timeout: int = 300) -> dict:
        """
        Execute a shell command.

        Args:
            command: Shell command to execute
            timeout: Maximum execution time in seconds

        Returns:
            dict with: success, stdout, stderr, returncode, duration_ms
        """
        started_at = int(time.time() * 1000)

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            duration_ms = int(time.time() * 1000) - started_at

            return {
                "success": result.returncode == 0,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
                "duration_ms": duration_ms,
                "timed_out": False,
            }
        except subprocess.TimeoutExpired:
            duration_ms = int(time.time() * 1000) - started_at
            return {
                "success": False,
                "stdout": "",
                "stderr": f"Command timed out after {timeout}s",
                "returncode": -1,
                "duration_ms": duration_ms,
                "timed_out": True,
            }
        except Exception as e:
            duration_ms = int(time.time() * 1000) - started_at
            return {
                "success": False,
                "stdout": "",
                "stderr": str(e),
                "returncode": -1,
                "duration_ms": duration_ms,
                "timed_out": False,
            }

    def execute_jarvis_action(self, task_id: str, action: dict) -> dict:
        """
        Queue a Jarvis action for execution.

        Writes a JSON file to the queue directory. Jarvis's main loop
        picks up these files and executes the TOOL_CALL.

        Args:
            task_id: The task ID this action belongs to
            action: Dict with skill, action, params (standard TOOL_CALL format)

        Returns:
            dict with: success, queue_file, message
        """
        timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)
        filename = f"task_{task_id}_{timestamp}.json"
        filepath = os.path.join(self.queue_dir, filename)

        queue_entry = {
            "task_id": task_id,
            "created_at": timestamp,
            "action": action,
            "status": "queued",
        }

        try:
            with open(filepath, "w") as f:
                json.dump(queue_entry, f, indent=2)
            return {
                "success": True,
                "queue_file": filepath,
                "message": f"Action queued at {filepath}",
            }
        except Exception as e:
            return {
                "success": False,
                "queue_file": None,
                "message": f"Failed to queue action: {str(e)}",
            }

    def execute(self, task: dict) -> dict:
        """
        Execute a task based on its type and configuration.

        Args:
            task: Task dict from TaskStore

        Returns:
            dict with execution result
        """
        started_at = int(datetime.now(timezone.utc).timestamp())

        if task.get("command"):
            # Shell execution
            result = self.execute_shell(task["command"])
            result["started_at"] = started_at
            result["finished_at"] = int(datetime.now(timezone.utc).timestamp())
            return result

        elif task.get("action") and isinstance(task["action"], dict):
            # Jarvis action dispatch
            result = self.execute_jarvis_action(task["id"], task["action"])
            result["started_at"] = started_at
            result["finished_at"] = int(datetime.now(timezone.utc).timestamp())
            return result

        else:
            return {
                "success": False,
                "stdout": "",
                "stderr": "No command or action configured for task",
                "returncode": -1,
                "started_at": started_at,
                "finished_at": int(datetime.now(timezone.utc).timestamp()),
                "duration_ms": 0,
            }

    def get_pending_actions(self) -> list[dict]:
        """Get all queued Jarvis actions that haven't been processed."""
        pending = []
        if not os.path.isdir(self.queue_dir):
            return pending

        for filename in sorted(os.listdir(self.queue_dir)):
            if not filename.endswith(".json"):
                continue
            filepath = os.path.join(self.queue_dir, filename)
            try:
                with open(filepath, "r") as f:
                    entry = json.load(f)
                if entry.get("status") == "queued":
                    pending.append(entry)
            except (json.JSONDecodeError, IOError):
                continue

        return pending

    def mark_action_processed(self, queue_file: str):
        """Mark a queued action as processed by updating its status."""
        try:
            with open(queue_file, "r") as f:
                entry = json.load(f)
            entry["status"] = "processed"
            entry["processed_at"] = int(datetime.now(timezone.utc).timestamp() * 1000)
            with open(queue_file, "w") as f:
                json.dump(entry, f, indent=2)
        except (json.JSONDecodeError, IOError):
            pass
