#!/usr/bin/env python3
"""
main.py — Task Scheduling Skill v1.0.0

Entry point for the task scheduler. Initializes components,
starts the scheduler engine, and launches the web UI server.

Usage:
    python main.py [--port PORT] [--host HOST] [--db DB_PATH]

CHANGELOG:
  v1.0.0 — Initial implementation
"""

import argparse
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")


def main():
    parser = argparse.ArgumentParser(description="Task Scheduler Skill")
    parser.add_argument("--port", type=int, default=int(os.environ.get("TASK_SCHEDULER_PORT", "9090")))
    parser.add_argument("--host", type=str, default=os.environ.get("TASK_SCHEDULER_HOST", "0.0.0.0"))
    parser.add_argument("--db", type=str, default=os.environ.get("TASK_SCHEDULER_DB", "/tmp/workspace/scheduler/tasks.db"))
    parser.add_argument("--static", type=str, default=os.environ.get("TASK_SCHEDULER_STATIC", ""))
    parser.add_argument("--no-scheduler", action="store_true", help="Don't start the background scheduler")
    args = parser.parse_args()

    # Determine static directory
    static_dir = args.static
    if not static_dir:
        # Default to the frontend build directory
        candidates = [
            os.path.join(os.path.dirname(__file__), "static"),
            "/app/output/jarvis/frontend/dist",
            "/app/output/jarvis/frontend",
        ]
        for candidate in candidates:
            if os.path.isdir(candidate):
                static_dir = candidate
                break
        if not static_dir:
            static_dir = os.path.join(os.path.dirname(__file__), "static")
            os.makedirs(static_dir, exist_ok=True)

    logger.info(f"Initializing task scheduler...")
    logger.info(f"  Database: {args.db}")
    logger.info(f"  Static dir: {static_dir}")
    logger.info(f"  Server: http://{args.host}:{args.port}")

    # Initialize components
    from . import TaskStore, SchedulerEngine, SkillAPI, run_server

    task_store = TaskStore(args.db)
    scheduler = SchedulerEngine(task_store)
    api = SkillAPI(task_store, scheduler)

    # Start scheduler
    if not args.no_scheduler:
        scheduler.start()
        logger.info("Background scheduler started")

    # Run server (blocking)
    logger.info(f"Starting web server on {args.host}:{args.port}")
    run_server(api, port=args.port, host=args.host, static_dir=static_dir)


if __name__ == "__main__":
    main()
