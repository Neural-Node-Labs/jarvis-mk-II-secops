"""
BlackboxBrain — Orchestrator Framework
Blueprint: "Blackbox Brain" system architecture document

Architecture:
  User Input
      │
  [1. Classifier]  ─→ CHAT  → direct LLM response
      │
  [2. Planner]     ─→ TASK  → DAG of isolated Task objects
      │
  [3. SwarmWorker] ─→ multi-threaded, ReAct max 3 loops per task
      │
  [4. Consolidator] → single unified report via LLM summary

Design principles:
  - SwarmWorkers are ephemeral: they DO NOT persist their inner ReAct scratchpad
  - The Orchestrator only stores final task artifacts (status + result)
  - Cost-bounded: re_act_max_loop = 3 per worker thread
  - Blackbox portable: process_message(str) → dict — wrappable behind any transport

version: 1.0.0
"""
import json
import uuid
import logging
import threading
import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger("blackbox_brain")

# ── Task definition ────────────────────────────────────────────────────────────

@dataclass
class Task:
    task_id:              str
    goal:                 str
    context:              dict
    validation_criteria:  str
    status:               str = "PENDING"   # PENDING | RUNNING | COMPLETED | FAILED | SKIPPED
    result:               Optional[str] = None
    error:                Optional[str] = None
    depends_on:           list = field(default_factory=list)   # task_ids this one waits for
    iterations_used:      int  = 0

# ── Swarm worker ──────────────────────────────────────────────────────────────

class SwarmWorker(threading.Thread):
    """
    Isolated ephemeral execution unit.

    Each worker:
      - Receives exactly ONE Task with all context it needs (no shared state)
      - Runs a ReAct loop capped at re_act_max_loop (default 3)
      - Throws away inner scratchpad on completion — only result + status surfaced
      - Calls the llm_fn synchronously (blocking) inside its own thread
    """

    def __init__(
        self,
        task:            Task,
        llm_fn:          Callable[[list, str], str],   # (messages, system) → str
        tool_fn:         Callable[[str, str, dict], dict],  # (skill, action, params) → result
        re_act_max_loop: int = 3,
    ):
        super().__init__(daemon=True)
        self.task            = task
        self.llm_fn          = llm_fn
        self.tool_fn         = tool_fn
        self.re_act_max_loop = re_act_max_loop

    def run(self):
        self.task.status = "RUNNING"
        logger.info("[swarm_worker] START task_id=%s goal=%.60s", self.task.task_id, self.task.goal)

        system_prompt = f"""You are a focused task executor.

TASK ID    : {self.task.task_id}
GOAL       : {self.task.goal}
CONTEXT    : {json.dumps(self.task.context, indent=2)}
VALIDATION : {self.task.validation_criteria}

Execute the task using TOOL_CALL blocks when needed.
When the goal is achieved, write TASK_COMPLETE and your result summary.
You have maximum {self.re_act_max_loop} reasoning iterations.
Be concise — the orchestrator only needs your final result, not your thinking process.
"""

        # Local ephemeral conversation — NOT shared with the main agent conversation
        local_conversation: list = [
            {"role": "user", "content": f"Execute this task:\n\nGOAL: {self.task.goal}\n\nCONTEXT: {json.dumps(self.task.context)}\n\nVALIDATION: {self.task.validation_criteria}"}
        ]

        TOOL_CALL_MARKER = "TOOL_CALL:"

        for iteration in range(1, self.re_act_max_loop + 1):
            self.task.iterations_used = iteration

            try:
                # ── REASON ────────────────────────────────────────────────────
                response = self.llm_fn(local_conversation, system_prompt)
                local_conversation.append({"role": "assistant", "content": response})

                # ── Completion check ───────────────────────────────────────────
                if "TASK_COMPLETE" in response or "task_complete" in response.lower():
                    self.task.result = self._extract_result(response)
                    self.task.status = "COMPLETED"
                    logger.info("[swarm_worker] COMPLETE task_id=%s iter=%d", self.task.task_id, iteration)
                    return

                # ── ACT — extract and execute tool calls ───────────────────────
                tool_calls = self._extract_tool_calls(response, TOOL_CALL_MARKER)
                if not tool_calls:
                    # No tools — agent reached a natural end
                    self.task.result = response.strip()
                    self.task.status = "COMPLETED"
                    logger.info("[swarm_worker] DONE (no tools) task_id=%s iter=%d", self.task.task_id, iteration)
                    return

                # ── OBSERVE — execute tools, build observation ──────────────────
                observations = []
                for call in tool_calls:
                    skill  = call.get("skill", "")
                    action = call.get("action", "")
                    params = call.get("params", {})
                    try:
                        result = self.tool_fn(skill, action, params)
                        if result.get("success"):
                            obs = f"[TOOL ✓ {skill}.{action}]\n{json.dumps(result.get('output'), indent=2, default=str)}"
                        else:
                            obs = f"[TOOL ✗ {skill}.{action} FAILED]\nError: {result.get('error')}\nDiagnose and adjust approach."
                    except Exception as tool_err:
                        obs = f"[TOOL ✗ {skill}.{action} EXCEPTION]\n{tool_err}"
                    observations.append(obs)

                # Feed observations back as user message
                obs_text = "\n\n".join(observations)
                local_conversation.append({"role": "user", "content": obs_text})

            except Exception as e:
                logger.error("[swarm_worker] ERROR task_id=%s iter=%d err=%s", self.task.task_id, iteration, e)
                local_conversation.append({"role": "user", "content": f"[ERROR]\n{e}\nAdapt and continue."})

        # Max iterations reached — surface whatever the last response was
        last_resp = local_conversation[-1].get("content", "") if local_conversation else "No result"
        self.task.result = f"[MAX_ITERATIONS_REACHED after {self.re_act_max_loop} loops]\n{last_resp}"
        self.task.status = "COMPLETED"
        logger.warning("[swarm_worker] MAX_ITER task_id=%s", self.task.task_id)

    def _extract_result(self, text: str) -> str:
        """Strip the TASK_COMPLETE marker and return the clean result."""
        for marker in ("TASK_COMPLETE", "task_complete"):
            if marker in text:
                idx = text.find(marker)
                return text[idx + len(marker):].strip().lstrip(":").strip()
        return text.strip()

    def _extract_tool_calls(self, text: str, marker: str) -> list:
        calls, seen, start = [], set(), 0
        while True:
            idx = text.find(marker, start)
            if idx == -1:
                break
            brace_start = text.find("{", idx + len(marker))
            if brace_start == -1:
                break
            depth, i = 0, brace_start
            while i < len(text):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            call = json.loads(text[brace_start:i+1])
                            if "skill" in call and "action" in call:
                                key = f"{call['skill']}:{call['action']}"
                                if key not in seen:
                                    seen.add(key)
                                    calls.append(call)
                        except json.JSONDecodeError:
                            pass
                        break
                i += 1
            start = idx + 1
        return calls


# ── BlackboxBrain ─────────────────────────────────────────────────────────────

class BlackboxBrain:
    """
    Stateful orchestrator blackbox.

    External interface:
        result = brain.process_message(message, user_id)
        # result = {"type": "chat"|"task_report", "data": str, "tasks": list}

    Internal state per instance:
        - conversation_memory: short-term chat history
        - task_registry:       all tasks ever submitted (status + result only)

    SwarmWorkers run in daemon threads — they clean up automatically.
    The brain never stores worker scratchpads, only final artifacts.
    """

    def __init__(
        self,
        llm_fn:          Callable[[list, str], str],
        tool_fn:         Callable[[str, str, dict], dict],
        re_act_max_loop: int = 3,
        max_parallel:    int = 8,
    ):
        self.llm_fn          = llm_fn
        self.tool_fn         = tool_fn
        self.re_act_max_loop = re_act_max_loop
        self.max_parallel    = max_parallel

        self.conversation_memory: list = []     # short-term: last N turns
        self.task_registry:       list = []     # all task artifacts (no scratchpads)
        self._lock = threading.Lock()

    # ── Public API ─────────────────────────────────────────────────────────────

    def process_message(self, message: str, user_id: str = "default") -> dict:
        """
        Synchronous entry point.
        Routes to CHAT or TASK path, returns unified output dict.
        """
        logger.info("[brain] process user=%s msg=%.80s", user_id, message)

        # 1. Classify
        category = self._classify(message)
        logger.info("[brain] classified as: %s", category)

        if category == "chat":
            response = self._handle_chat(message)
            self._update_memory("user", message)
            self._update_memory("assistant", response)
            return {"type": "chat", "data": response, "tasks": []}

        # 2. Plan — generate DAG of Task objects
        tasks = self._generate_plan(message, user_id)
        if not tasks:
            # Planner returned empty — fall back to chat
            response = self._handle_chat(message)
            return {"type": "chat", "data": response, "tasks": []}

        logger.info("[brain] plan generated: %d tasks", len(tasks))

        # 3. Execute swarm (multi-threaded, dependency-ordered)
        executed = self._run_swarm(tasks)

        # 4. Consolidate
        report   = self._consolidate(executed)
        summary  = self._llm_summarize(report, message)

        with self._lock:
            self.task_registry.extend(executed)

        self._update_memory("user", message)
        self._update_memory("assistant", summary)

        return {
            "type":  "task_report",
            "data":  summary,
            "tasks": [self._task_to_dict(t) for t in executed],
        }

    def get_task_registry(self) -> list:
        with self._lock:
            return [self._task_to_dict(t) for t in self.task_registry]

    def get_active_tasks(self) -> list:
        with self._lock:
            return [self._task_to_dict(t) for t in self.task_registry if t.status == "RUNNING"]

    def abort_task(self, task_id: str) -> bool:
        """Mark a task as FAILED (threads are daemon — they'll terminate naturally)."""
        with self._lock:
            for t in self.task_registry:
                if t.task_id == task_id and t.status == "RUNNING":
                    t.status = "FAILED"
                    t.error  = "Aborted by operator"
                    return True
        return False

    def reset(self):
        with self._lock:
            self.conversation_memory = []
            self.task_registry       = []

    # ── Layer 1: Classifier ────────────────────────────────────────────────────

    def _classify(self, message: str) -> str:
        """
        Lightweight LLM call to classify intent.
        Returns 'chat' or 'task'.
        """
        system = """You are an intent classifier. Analyse the user message.
Return ONLY a JSON object: {"category": "chat"} or {"category": "task"}

Rules:
- "task"  → request requires executing actions, creating files, running commands,
             multi-step operations, research + produce output, build/test/deploy
- "chat"  → questions, explanations, advice, conversation, single-sentence answers
"""
        classify_conv = [{"role": "user", "content": message}]
        try:
            raw = self.llm_fn(classify_conv, system)
            data = json.loads(raw.strip())
            cat = data.get("category", "chat")
            return cat if cat in ("chat", "task") else "chat"
        except Exception:
            # If classification fails, check heuristics
            task_keywords = ["create", "build", "write", "run", "execute", "install",
                             "scan", "test", "deploy", "generate", "search", "find and"]
            lower = message.lower()
            return "task" if any(k in lower for k in task_keywords) else "chat"

    # ── Layer 2: Chat path ────────────────────────────────────────────────────

    def _handle_chat(self, message: str) -> str:
        conv = self.conversation_memory[-10:] + [{"role": "user", "content": message}]
        system = "You are a helpful, concise AI assistant. Respond directly."
        try:
            return self.llm_fn(conv, system)
        except Exception as e:
            return f"[Error generating response: {e}]"

    # ── Layer 3: Planner ──────────────────────────────────────────────────────

    def _generate_plan(self, message: str, user_id: str) -> list:
        """
        LLM generates a structured execution plan: list of Task objects.
        Each task is self-contained with explicit context + validation criteria.
        """
        system = f"""You are a task planner. Break down the user request into isolated, parallel-safe execution tasks.

Return ONLY a JSON array — no markdown, no explanation:
[
  {{
    "task_id": "TSK-001",
    "goal": "Clear, actionable objective for one worker",
    "context": {{"key": "any static data the worker needs"}},
    "validation_criteria": "How to verify this specific task succeeded",
    "depends_on": []
  }}
]

Rules:
- Each task MUST be self-contained — no task should assume another ran first unless listed in depends_on
- Maximum 8 tasks per plan
- depends_on lists task_ids that must complete first
- context must include ALL data the worker needs (no shared state)
- goal must be a single clear actionable statement
"""
        conv = [{"role": "user", "content": f"Plan this: {message}"}]
        try:
            raw   = self.llm_fn(conv, system)
            clean = raw.strip()
            # Strip markdown fences if present
            if clean.startswith("```"):
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            plan_data = json.loads(clean)
            tasks = []
            for item in plan_data[:self.max_parallel]:
                tasks.append(Task(
                    task_id             = item.get("task_id", f"TSK-{str(uuid.uuid4())[:6]}"),
                    goal                = item.get("goal", ""),
                    context             = item.get("context", {}),
                    validation_criteria = item.get("validation_criteria", "Task completed successfully"),
                    depends_on          = item.get("depends_on", []),
                ))
            return tasks
        except Exception as e:
            logger.error("[brain] planner failed: %s", e)
            # Fallback: single task
            return [Task(
                task_id             = "TSK-001",
                goal                = message,
                context             = {"user_id": user_id},
                validation_criteria = "Request fulfilled",
            )]

    # ── Layer 4: Swarm execution ──────────────────────────────────────────────

    def _run_swarm(self, tasks: list) -> list:
        """
        Execute tasks in dependency order using daemon threads.
        Tasks with no unmet dependencies run in parallel.
        Tasks with dependencies wait for their prereqs to complete.
        """
        task_map    = {t.task_id: t for t in tasks}
        completed   = set()
        all_task_ids = set(task_map.keys())

        while completed != all_task_ids:
            # Find tasks ready to run (not started, deps all done)
            ready = [
                t for t in tasks
                if t.status == "PENDING"
                and all(dep in completed for dep in t.depends_on)
            ]

            if not ready:
                # Check for deadlock — no PENDING tasks but not all done
                remaining = [t for t in tasks if t.status == "PENDING"]
                if remaining:
                    logger.warning("[brain] dependency deadlock — marking %d tasks SKIPPED", len(remaining))
                    for t in remaining:
                        t.status = "SKIPPED"
                        t.error  = "Dependency deadlock"
                break

            # Spawn worker threads for ready batch
            workers = []
            for task in ready:
                w = SwarmWorker(task, self.llm_fn, self.tool_fn, self.re_act_max_loop)
                workers.append(w)
                w.start()
                logger.info("[brain] spawned worker task_id=%s", task.task_id)

            # Wait for this batch to complete
            for w in workers:
                w.join(timeout=300)  # 5-minute hard timeout per task
                if w.is_alive():
                    w.task.status = "FAILED"
                    w.task.error  = "Worker timeout (300s)"
                    logger.error("[brain] worker timeout task_id=%s", w.task.task_id)

            # Mark batch as completed for dependency resolution
            for task in ready:
                completed.add(task.task_id)

        return tasks

    # ── Layer 5: Consolidation ────────────────────────────────────────────────

    def _consolidate(self, tasks: list) -> str:
        lines = ["# Swarm Execution Report\n"]
        for t in tasks:
            status_icon = {"COMPLETED": "✓", "FAILED": "✗", "SKIPPED": "⊘"}.get(t.status, "?")
            lines.append(f"## [{status_icon}] {t.task_id} — {t.goal}")
            lines.append(f"**Status:** {t.status}  |  **Iterations:** {t.iterations_used}")
            if t.result:
                lines.append(f"**Result:**\n{t.result}")
            if t.error:
                lines.append(f"**Error:** {t.error}")
            lines.append("")
        return "\n".join(lines)

    def _llm_summarize(self, report: str, original_message: str) -> str:
        system = """You are a report synthesiser. Given a multi-task execution report,
produce a clear, concise summary for the operator.
Lead with the overall outcome. Then list key results per task.
Be factual and direct. No filler."""
        conv = [
            {"role": "user", "content": f"Original request: {original_message}\n\nExecution report:\n{report}"}
        ]
        try:
            return self.llm_fn(conv, system)
        except Exception as e:
            return f"[Summary generation failed: {e}]\n\nRaw report:\n{report}"

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _update_memory(self, role: str, content: str):
        self.conversation_memory.append({"role": role, "content": content})
        if len(self.conversation_memory) > 40:
            self.conversation_memory = self.conversation_memory[-40:]

    def _task_to_dict(self, t: Task) -> dict:
        return {
            "task_id":             t.task_id,
            "goal":                t.goal,
            "status":              t.status,
            "result":              t.result,
            "error":               t.error,
            "iterations_used":     t.iterations_used,
            "validation_criteria": t.validation_criteria,
            "depends_on":          t.depends_on,
        }


# ── Brain registry (per user_id) ───────────────────────────────────────────────

_BRAINS: dict[str, BlackboxBrain] = {}
_BRAINS_LOCK = threading.Lock()


def get_brain(user_id: str, llm_fn: Callable, tool_fn: Callable,
              re_act_max_loop: int = 3) -> BlackboxBrain:
    """Get or create a BlackboxBrain for a given user_id."""
    with _BRAINS_LOCK:
        if user_id not in _BRAINS:
            _BRAINS[user_id] = BlackboxBrain(
                llm_fn=llm_fn,
                tool_fn=tool_fn,
                re_act_max_loop=re_act_max_loop,
            )
        return _BRAINS[user_id]


def destroy_brain(user_id: str):
    with _BRAINS_LOCK:
        _BRAINS.pop(user_id, None)
