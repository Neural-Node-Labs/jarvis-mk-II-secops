#!/usr/bin/env python3
"""
server.py — Task Scheduling Skill v1.0.0

HTTP server that exposes the SkillAPI via REST endpoints
and serves the web UI dashboard.

CHANGELOG:
  v1.0.0 — Initial implementation
"""

import json
import logging
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from .task_store import TaskStore
from .scheduler_engine import SchedulerEngine
from .skill_api import SkillAPI

logger = logging.getLogger("task_scheduler_server")

PORT = int(os.environ.get("TASK_SCHEDULER_PORT", "9090"))
HOST = os.environ.get("TASK_SCHEDULER_HOST", "0.0.0.0")
STATIC_DIR = os.environ.get("TASK_SCHEDULER_STATIC",
                            os.path.join(os.path.dirname(__file__), "static"))


class TaskSchedulerHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the task scheduler API and UI."""

    # Shared state (set by server)
    api: SkillAPI = None
    static_dir: str = STATIC_DIR

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        params = parse_qs(parsed.query)

        # API routes
        if path == "/api/status":
            self._json_response(self.api.get_scheduler_status())
        elif path == "/api/stats":
            self._json_response(self.api.get_stats())
        elif path == "/api/tasks":
            status_filter = params.get("status", [None])[0]
            self._json_response(self.api.list_tasks(status_filter))
        elif path.startswith("/api/tasks/"):
            task_id = path.split("/")[-1]
            if path.endswith("/history"):
                task_id = path.split("/")[-2]
                limit = int(params.get("limit", [50])[0])
                self._json_response(self.api.get_task_history(task_id, limit))
            else:
                self._json_response(self.api.get_task(task_id))
        elif path == "/api/queue":
            self._json_response({"success": True, "queue": []})
        else:
            # Serve static files
            self._serve_static(path)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b"{}"
        data = json.loads(body) if body else {}

        if path == "/api/schedule":
            result = self.api.schedule(
                name=data.get("name", ""),
                type=data.get("type", "oneshot"),
                schedule_expr=data.get("schedule_expr"),
                command=data.get("command"),
                action=data.get("action"),
                max_runs=data.get("max_runs", -1),
                metadata=data.get("metadata"),
            )
            self._json_response(result, status=201 if result.get("success") else 400)

        elif path.startswith("/api/tasks/"):
            parts = path.split("/")
            task_id = parts[3]

            if len(parts) > 4 and parts[4] == "cancel":
                self._json_response(self.api.cancel_task(task_id))
            elif len(parts) > 4 and parts[4] == "pause":
                self._json_response(self.api.pause_task(task_id))
            elif len(parts) > 4 and parts[4] == "resume":
                self._json_response(self.api.resume_task(task_id))
            elif len(parts) > 4 and parts[4] == "delete":
                self._json_response(self.api.delete_task(task_id))
            else:
                self._json_response({"success": False, "error": "Unknown action"}, 404)

        elif path == "/api/scheduler/start":
            self.api.scheduler.start()
            self._json_response(self.api.get_scheduler_status())

        elif path == "/api/scheduler/stop":
            self.api.scheduler.stop()
            self._json_response(self.api.get_scheduler_status())

        else:
            self._json_response({"success": False, "error": "Not found"}, 404)

    def _json_response(self, data: dict, status: int = 200):
        """Send a JSON response."""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode())

    def _serve_static(self, path: str):
        """Serve static files from the static directory."""
        if path == "" or path == "/":
            path = "/index.html"

        filepath = os.path.join(self.static_dir, path.lstrip("/"))

        # Security: prevent directory traversal
        real_path = os.path.realpath(filepath)
        real_static = os.path.realpath(self.static_dir)
        if not real_path.startswith(real_static):
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"Forbidden")
            return

        if not os.path.isfile(filepath):
            # Fallback to index.html for SPA routing
            filepath = os.path.join(self.static_dir, "index.html")
            if not os.path.isfile(filepath):
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not found")
                return

        # Determine content type
        ext = os.path.splitext(filepath)[1].lower()
        content_types = {
            ".html": "text/html",
            ".css": "text/css",
            ".js": "application/javascript",
            ".json": "application/json",
            ".png": "image/png",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
        }
        content_type = content_types.get(ext, "application/octet-stream")

        try:
            with open(filepath, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except IOError:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")

    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        """Override default logging to use our logger."""
        logger.info(f"{self.address_string()} - {format % args}")


def create_server(api: SkillAPI, port: int = PORT,
                  host: str = HOST, static_dir: str = STATIC_DIR):
    """Create and configure the HTTP server."""
    TaskSchedulerHandler.api = api
    TaskSchedulerHandler.static_dir = static_dir
    server = HTTPServer((host, port), TaskSchedulerHandler)
    logger.info(f"Task scheduler server listening on http://{host}:{port}")
    return server


def run_server(api: SkillAPI, port: int = PORT,
               host: str = HOST, static_dir: str = STATIC_DIR):
    """Run the HTTP server (blocking)."""
    server = create_server(api, port, host, static_dir)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server shutting down")
        server.shutdown()
