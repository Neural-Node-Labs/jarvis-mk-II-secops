#!/usr/bin/env python3
"""
task_store.py — Task Scheduling Skill v1.0.0

SQLite-backed persistent task storage with full CRUD operations.
Schema supports cron recurring, one-shot, and queue-based tasks.

CHANGELOG:
  v1.0.0 — Initial implementation: schema, CRUD, migration
"""

import sqlite3
import json
import os
import threading
from datetime import datetime, timezone
from typing import Optional, Any

DB_PATH = os.environ.get("TASK_SCHEDULER_DB", "/app/workspace/scheduler/tasks.db")

# Schema version for migration tracking
SCHEMA_VERSION = 1

# ---- Task Status Constants ----
STATUS_PENDING   = "pending"
STATUS_ACTIVE    = "active"
STATUS_RUNNING   = "running"
STATUS_PAUSED    = "paused"
STATUS_CANCELLED = "cancelled"
STATUS_COMPLETED = "completed"
STATUS_FAILED    = "failed"

VALID_STATUSES = {
    STATUS_PENDING, STATUS_ACTIVE, STATUS_RUNNING,
    STATUS_PAUSED, STATUS_CANCELLED, STATUS_COMPLETED, STATUS_FAILED
}

# ---- Task Type Constants ----
TYPE_CRON    = "cron"
TYPE_ONESHOT = "oneshot"
TYPE_QUEUE   = "queue"

VALID_TYPES = {TYPE_CRON, TYPE_ONESHOT, TYPE_QUEUE}


class TaskStore:
    """Thread-safe SQLite-backed task storage."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._local = threading.local()
        self._lock = threading.Lock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Get thread-local connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA foreign_keys=ON")
        return self._local.conn

    def _init_db(self):
        """Initialize database schema and run migrations."""
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL CHECK(type IN ('cron','oneshot','queue')),
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','active','running','paused','cancelled','completed','failed')),
                schedule_expr TEXT,
                command TEXT,
                action TEXT,
                created_at INTEGER NOT NULL,
                next_run_at INTEGER,
                last_run_at INTEGER,
                run_count INTEGER NOT NULL DEFAULT 0,
                max_runs INTEGER NOT NULL DEFAULT -1,
                metadata TEXT DEFAULT '{}',
                created_by TEXT DEFAULT 'jarvis'
            );

            CREATE TABLE IF NOT EXISTS task_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                status TEXT NOT NULL,
                output TEXT,
                error TEXT,
                started_at INTEGER NOT NULL,
                finished_at INTEGER,
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_tasks_next_run ON tasks(next_run_at);
            CREATE INDEX IF NOT EXISTS idx_history_task_id ON task_history(task_id);
        """)
        conn.commit()
        self._migrate()

    def _migrate(self):
        """Run schema migrations based on version."""
        conn = self._get_conn()
        cursor = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version")
        current_version = cursor.fetchone()[0]

        if current_version < SCHEMA_VERSION:
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, int(datetime.now(timezone.utc).timestamp()))
            )
            conn.commit()

    # ---- CRUD Operations ----

    def create_task(self, task_data: dict) -> dict:
        """Create a new task. Returns the created task dict."""
        import uuid
        task_id = str(uuid.uuid4())
        now = int(datetime.now(timezone.utc).timestamp())

        task = {
            "id": task_id,
            "name": task_data.get("name", ""),
            "type": task_data.get("type", TYPE_ONESHOT),
            "status": STATUS_ACTIVE,
            "schedule_expr": task_data.get("schedule_expr"),
            "command": task_data.get("command"),
            "action": json.dumps(task_data.get("action", {})),
            "created_at": now,
            "next_run_at": task_data.get("next_run_at"),
            "last_run_at": None,
            "run_count": 0,
            "max_runs": task_data.get("max_runs", -1),
            "metadata": json.dumps(task_data.get("metadata", {})),
            "created_by": task_data.get("created_by", "jarvis"),
        }

        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """INSERT INTO tasks (id, name, type, status, schedule_expr, command, action,
                   created_at, next_run_at, last_run_at, run_count, max_runs, metadata, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (task["id"], task["name"], task["type"], task["status"],
                 task["schedule_expr"], task["command"], task["action"],
                 task["created_at"], task["next_run_at"], task["last_run_at"],
                 task["run_count"], task["max_runs"], task["metadata"], task["created_by"])
            )
            conn.commit()

        return self.get_task(task_id)

    def get_task(self, task_id: str) -> Optional[dict]:
        """Get a single task by ID."""
        conn = self._get_conn()
        cursor = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        return self._row_to_dict(row) if row else None

    def list_tasks(self, status_filter: Optional[str] = None) -> list[dict]:
        """List all tasks, optionally filtered by status."""
        conn = self._get_conn()
        if status_filter and status_filter in VALID_STATUSES:
            cursor = conn.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC",
                (status_filter,)
            )
        else:
            cursor = conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC"
            )
        return [self._row_to_dict(row) for row in cursor.fetchall()]

    def update_task(self, task_id: str, updates: dict) -> Optional[dict]:
        """Update a task's fields. Returns updated task or None if not found."""
        allowed_fields = {
            "name", "type", "status", "schedule_expr", "command",
            "action", "next_run_at", "last_run_at", "run_count",
            "max_runs", "metadata"
        }
        set_clauses = []
        params = []

        for key, value in updates.items():
            if key in allowed_fields:
                if key == "action" and isinstance(value, dict):
                    value = json.dumps(value)
                if key == "metadata" and isinstance(value, dict):
                    value = json.dumps(value)
                set_clauses.append(f"{key} = ?")
                params.append(value)

        if not set_clauses:
            return self.get_task(task_id)

        params.append(task_id)

        with self._lock:
            conn = self._get_conn()
            conn.execute(
                f"UPDATE tasks SET {', '.join(set_clauses)} WHERE id = ?",
                params
            )
            conn.commit()

        return self.get_task(task_id)

    def delete_task(self, task_id: str) -> bool:
        """Delete a task and its history. Returns True if deleted."""
        with self._lock:
            conn = self._get_conn()
            cursor = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            conn.execute("DELETE FROM task_history WHERE task_id = ?", (task_id,))
            conn.commit()
        return cursor.rowcount > 0

    # ---- Due Task Query ----

    def get_due_tasks(self) -> list[dict]:
        """Get all active tasks whose next_run_at <= now."""
        now = int(datetime.now(timezone.utc).timestamp())
        conn = self._get_conn()
        cursor = conn.execute(
            """SELECT * FROM tasks
               WHERE status = 'active'
                 AND next_run_at IS NOT NULL
                 AND next_run_at <= ?
                 AND (max_runs = -1 OR run_count < max_runs)
               ORDER BY next_run_at ASC""",
            (now,)
        )
        return [self._row_to_dict(row) for row in cursor.fetchall()]

    # ---- History Operations ----

    def add_history(self, task_id: str, status: str, output: str = "",
                    error: str = "", started_at: Optional[int] = None,
                    finished_at: Optional[int] = None) -> int:
        """Add a history entry for a task execution."""
        now = int(datetime.now(timezone.utc).timestamp())
        started_at = started_at or now

        conn = self._get_conn()
        cursor = conn.execute(
            """INSERT INTO task_history (task_id, status, output, error, started_at, finished_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (task_id, status, output, error, started_at, finished_at)
        )
        conn.commit()
        return cursor.lastrowid

    def get_history(self, task_id: str, limit: int = 50) -> list[dict]:
        """Get execution history for a task."""
        conn = self._get_conn()
        cursor = conn.execute(
            """SELECT * FROM task_history
               WHERE task_id = ?
               ORDER BY started_at DESC
               LIMIT ?""",
            (task_id, limit)
        )
        return [dict(row) for row in cursor.fetchall()]

    # ---- Stats ----

    def get_stats(self) -> dict:
        """Get scheduler statistics."""
        conn = self._get_conn()
        cursor = conn.execute(
            """SELECT
                 COUNT(*) as total,
                 SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) as active,
                 SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) as running,
                 SUM(CASE WHEN status = 'paused' THEN 1 ELSE 0 END) as paused,
                 SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                 SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed
               FROM tasks"""
        )
        stats = dict(cursor.fetchone())
        stats["total"] = stats["total"] or 0
        for k in ["active", "running", "paused", "completed", "failed"]:
            stats[k] = stats[k] or 0
        return stats

    def get_total_history_count(self) -> int:
        """Get total number of history entries."""
        conn = self._get_conn()
        cursor = conn.execute("SELECT COUNT(*) FROM task_history")
        return cursor.fetchone()[0] or 0

    # ---- Helpers ----

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        """Convert sqlite3.Row to dict with parsed JSON fields."""
        d = dict(row)
        if isinstance(d.get("action"), str):
            try:
                d["action"] = json.loads(d["action"])
            except (json.JSONDecodeError, TypeError):
                pass
        if isinstance(d.get("metadata"), str):
            try:
                d["metadata"] = json.loads(d["metadata"])
            except (json.JSONDecodeError, TypeError):
                pass
        return d

    def close(self):
        """Close the database connection."""
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None
