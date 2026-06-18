"""
Blackbox Brain — Central Nervous System
Ported from: brain-solution-design.md + blackbox_brain.py (reference blueprint)

Architecture:
  User message
      │
  [0. Attachment + memory injection]   (unchanged from the old Agent)
      │
  react=False ─────────────────────────────────────────────► [Quick mode]
      │                                                       one LLM turn,
      │                                                       tools allowed once,
  react=True                                                  no classification.
      │                                                       (cheap default path
      │                                                        for plain /api/chat)
      ▼
  force_single_task=True ──────────────► [Deep task]  ONE isolated worker,
      │                                   extended iteration cap. This is the
      │                                   escape hatch for genuinely sequential,
      │                                   stateful work (see note below) — used
      │                                   by /api/evolve.
      ▼
  [1. Classifier] ──CHAT──► [2a. Chat path] single streamed LLM reply, no tools
      │
     TASK
      │
  [2b. Planner] → DAG of isolated Task objects (≤ max_parallel)
      │
  [3. Swarm] → asyncio tasks, dependency-ordered batches, ReAct loop capped at
      │        re_act_max_loop per worker, concurrency capped at max_parallel
      ▼
  [4. Consolidation] → markdown report → LLM summary (streamed) → done

Why ported to asyncio instead of the reference blueprint's threading.Thread:
  This whole codebase (FastAPI, LLMRouter, SkillRegistry) is async/httpx-based.
  Spinning up real OS threads that each block on synchronous LLM/tool calls
  would fight the event loop and the GIL for no benefit — asyncio tasks give
  the same "isolated, concurrent, ephemeral" worker model the design doc
  asks for, with proper non-blocking I/O instead.

A known, deliberate trade-off vs. the reference design (flagged here, not
hidden): the Planner decomposes a request into independent, parallel-safe
tasks capped at a small ReAct loop (re_act_max_loop, default 3) — exactly
as specified ("Predictable Cost & Time Bounds"). That's a poor fit for
something like the Self-Evolution pipeline, which is one long, deeply
SEQUENTIAL, stateful build (blueprint → approval → implement → test →
deploy → verify) — it can't be meaningfully split into 8 independent
parallel tasks. For that one case, `force_single_task=True` bypasses the
Planner/Swarm entirely and runs a single worker with a much higher
iteration cap, instead of silently degrading evolution's depth to 3 steps.
See main.py's /api/evolve call site.

This module IS the new central nervous system. core/agent.py is now a thin
compatibility re-export (`Agent = BlackboxBrain`) so every existing call
site — main.py's session registry, the JarvisMKII multi-task skill, the
scheduler's ai_call tasks, the Telegram bridge, /api/evolve — keeps working
unchanged: same constructor signature, same chat_stream()/confirm_action()
event protocol (token / react_status / tool_call / tool_result /
confirm_needed / done / error).

version: 1.0.0
"""
import json
import logging
import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Optional

from core.llm_router import LLMRouter, LLMConfig
from core.skill_registry import SkillRegistry
from core.prompt_builder import build_system_prompt, DEFAULT_PERSONA
from core.memory_manager import MemoryManager, detect_retrieval_request

logger = logging.getLogger("blackbox_brain")

# ── Constants ─────────────────────────────────────────────────────────────────
DEFAULT_RE_ACT_MAX_LOOP = 3     # per-worker ReAct cap — the design's cost/time bound
DEEP_TASK_MAX_ITERATIONS = 30   # for force_single_task=True call sites (e.g. /api/evolve) —
                                 # deliberately separate from DEFAULT_RE_ACT_MAX_LOOP above.
                                 # NOTE: core/agent.py's facade re-exports DEFAULT_RE_ACT_MAX_LOOP
                                 # as `REACT_MAX_ITERATIONS` purely so the NAME keeps resolving for
                                 # old call sites — its VALUE is now 3, not the old single-track
                                 # agent's 30. Anything that wants "the deep-task budget" must use
                                 # THIS constant, not REACT_MAX_ITERATIONS.
DEFAULT_MAX_PARALLEL    = 8     # planner task cap AND swarm concurrency cap
SWARM_WORKER_TIMEOUT_S  = 300   # hard per-task timeout (ported from the reference
                                 # blueprint's `w.join(timeout=300)`)
MAX_CONVERSATION_TURNS  = 80    # same pruning bound as the old Agent
TOOL_CALL_MARKER        = "TOOL_CALL:"

COMPLETION_SIGNALS = {
    "task_complete", "task complete", "task is complete", "task completed",
    "all done", "i have completed", "successfully completed",
    "the task is done", "work is complete",
}

CLASSIFIER_SYSTEM_PROMPT = """You are an intent classifier. Analyse the user message.
Return ONLY a JSON object: {"category": "chat"} or {"category": "task"}

Rules:
- "task"  → request requires executing actions, creating files, running commands,
             multi-step operations, research + produce output, build/test/deploy
- "chat"  → questions, explanations, advice, conversation, single-sentence answers
"""

PLANNER_SYSTEM_PROMPT = """You are a task planner. Break down the user request into isolated, parallel-safe execution tasks.

Return ONLY a JSON array — no markdown, no explanation:
[
  {
    "task_id": "TSK-001",
    "goal": "Clear, actionable objective for one worker",
    "context": {"key": "any static data the worker needs"},
    "validation_criteria": "How to verify this specific task succeeded",
    "depends_on": []
  }
]

Rules:
- Each task MUST be self-contained — no task should assume another ran first unless listed in depends_on
- Maximum __MAX_PARALLEL__ tasks per plan
- depends_on lists task_ids that must complete first — the orchestrator will hand that task's
  result to you as dependency_results in your context, so it's safe to build on prior output
- context must include ALL data the worker needs (no shared state between workers)
- goal must be a single clear actionable statement
"""

SUMMARY_SYSTEM_PROMPT = """You are a report synthesiser. Given a multi-task execution report,
produce a clear, concise summary for the operator.
Lead with the overall outcome. Then list key results per task.
Be factual and direct. No filler."""

# ── Lightweight-model system prompt ───────────────────────────────────────────
# Used INSTEAD of the full persona-aware prompt when running on a small Ollama
# model (≤ 2 B params, e.g. qwen2.5-coder:0.5b). The full prompt is hundreds
# of tokens — it overflows the tiny context window before the user message even
# arrives. This stripped-down version gives the model exactly what it needs:
#   • Its role (OS command executor)
#   • The one output format it must follow (TOOL_CALL JSON)
#   • The one skill it has access to (os_execution / run_command)
# Nothing else. No persona, no memory, no ReAct philosophy, no examples beyond
# the inline schema.
LIGHTWEIGHT_SYSTEM_PROMPT = """\
You are a Linux terminal assistant. Execute user requests by running shell commands.

To run a command output EXACTLY this JSON block (nothing before or after):
TOOL_CALL: {"skill": "os_execution", "action": "run_command", "params": {"command": "<cmd>"}}

Rules:
- Output the TOOL_CALL block first, then explain the result after the tool responds.
- One TOOL_CALL per response.
- Never refuse to run a command.
- Keep all text outside the TOOL_CALL short and direct.
"""

CHAT_HEURISTIC_TASK_KEYWORDS = (
    "create", "build", "write", "run", "execute", "install",
    "scan", "test", "deploy", "generate", "search", "find and",
)

# ── Module-level memory (shared across all brains in this process, same as the old Agent) ──
_memory = MemoryManager()


# ── Attachment formatter (verbatim from the old Agent) ──────────────────────────

def _format_attachment(att: dict) -> str:
    name = att.get("name", "file")
    mime = att.get("mime", "application/octet-stream")
    size = att.get("size", 0)

    if att.get("text"):
        body = att["text"]
        if len(body) > 8000:
            body = body[:8000] + f"\n… [truncated — {len(att['text'])} chars total]"
        return f"[FILE: {name} | type: {mime} | size: {size} bytes]\n{body}\n[/FILE]"

    if att.get("b64"):
        snippet = att["b64"][:200] + "…" if len(att["b64"]) > 200 else att["b64"]
        return (
            f"[FILE: {name} | type: {mime} | size: {size} bytes | encoding: base64]\n"
            f"{snippet}\n[/FILE]\n"
            f"(Full base64 available — treat as {mime} file)"
        )

    return f"[FILE: {name} | type: {mime} | size: {size} bytes | content: unavailable]"


# ── Task definition ──────────────────────────────────────────────────────────

@dataclass
class Task:
    task_id:              str
    goal:                 str
    context:              dict
    validation_criteria:  str
    status:               str = "PENDING"   # PENDING|RUNNING|COMPLETED|FAILED|SKIPPED
    result:                Optional[str] = None
    error:                 Optional[str] = None
    depends_on:            list = field(default_factory=list)
    iterations_used:       int  = 0


# ── BlackboxBrain ─────────────────────────────────────────────────────────────

class BlackboxBrain:
    """
    Drop-in replacement for the old single-track Agent. Same constructor,
    same chat_stream()/confirm_action() contract — see core/agent.py.
    """

    def __init__(self, config: LLMConfig, registry: SkillRegistry, user_id: str = "default",
                 re_act_max_loop: int = DEFAULT_RE_ACT_MAX_LOOP, max_parallel: int = DEFAULT_MAX_PARALLEL):
        self.llm      = LLMRouter(config)
        self.registry = registry
        self.user_id  = user_id
        self.re_act_max_loop_default = re_act_max_loop
        self.max_parallel            = max_parallel

        self.conversation:     list = []     # short-term chat memory (compat w/ old Agent)
        self.pending_confirms: dict = {}     # confirm_id → (skill, action, params)
        self._confirm_counter: int  = 0
        self.persona:          str  = DEFAULT_PERSONA
        self.task_registry:    list = []     # all task artifacts ever run — no scratchpads

    @property
    def _lightweight(self) -> bool:
        """True when the backing LLM is a small model that needs minimal prompting."""
        return getattr(self.llm, "lightweight", False)

    def reset(self):
        """Clear conversation + task state. Memory file on disk is preserved."""
        self.conversation     = []
        self.pending_confirms = {}
        self._confirm_counter = 0
        self.task_registry    = []

    def _append(self, role: str, content: str):
        self.conversation.append({"role": role, "content": content})
        if len(self.conversation) > MAX_CONVERSATION_TURNS:
            self.conversation = self.conversation[:1] + self.conversation[-(MAX_CONVERSATION_TURNS - 1):]

    # ── Public entry points ────────────────────────────────────────────────────

    async def chat_stream(
        self,
        user_message:      str,
        react:              bool = False,
        auto_confirm:       bool = False,
        attachments:        Optional[list] = None,
        memory_enabled:      bool = True,
        persona:             str  = DEFAULT_PERSONA,
        re_act_max_loop:     Optional[int] = None,
        force_single_task:   bool = False,
    ) -> AsyncGenerator[dict, None]:
        """
        react=False         → Quick mode: one LLM turn, tools allowed once (same
                               as the old Agent's single-cycle — this is what plain
                               /api/chat uses by default).
        react=True           → Classifier decides chat vs. task. Chat → one
                               streamed reply, no tools. Task → Planner + Swarm
                               + Consolidation.
        force_single_task=True → Skip the Planner; run ONE worker with an
                               extended iteration cap (re_act_max_loop, e.g. 30).
                               For deep, sequential, stateful work that can't be
                               split into independent parallel tasks. Used by
                               /api/evolve.
        """
        if attachments:
            blocks = [_format_attachment(a) for a in attachments]
            user_message = "\n\n".join(blocks) + "\n\n" + user_message

        self.persona = persona

        # ── Lightweight-model fast path ────────────────────────────────────────
        # Small Ollama models (≤ 2 B params) choke on the full persona prompt and
        # memory context. Bypass all of that: no memory read/write, no persona
        # system prompt, no classifier, no planner/swarm — just a single ReAct
        # cycle with the minimal OS-execution prompt. Memory is deliberately
        # skipped (not just suppressed) because injecting past turns would push
        # the model over its usable context budget immediately.
        if self._lightweight:
            async for event in self._single_cycle(
                user_message, auto_confirm, LIGHTWEIGHT_SYSTEM_PROMPT
            ):
                yield event
            return
        # ── End lightweight fast path ──────────────────────────────────────────

        n = detect_retrieval_request(user_message) if memory_enabled else None
        if memory_enabled and n is not None:
            history = _memory.retrieve_last_n(self.user_id, n)
            system_prompt = build_system_prompt(memory_context=history, persona=persona)
            logger.info("[memory_injected] user=%s turns=%d persona=%s", self.user_id, n, persona)
        else:
            system_prompt = build_system_prompt(persona=persona)

        if not react:
            async for event in self._single_cycle(user_message, auto_confirm, system_prompt):
                yield event
            return

        effective_cap = re_act_max_loop or self.re_act_max_loop_default

        if force_single_task:
            async for event in self._run_task_pipeline(
                user_message, auto_confirm, persona, effective_cap, skip_planner=True
            ):
                yield event
            return

        yield {"type": "react_status", "data": {"iteration": 0, "max": 0, "phase": "classifying", "healing": False}}
        category = await self._classify(user_message)
        logger.info("[brain_classified] user=%s category=%s", self.user_id, category)

        if category == "chat":
            async for event in self._handle_chat_stream(user_message, system_prompt):
                yield event
        else:
            async for event in self._run_task_pipeline(
                user_message, auto_confirm, persona, effective_cap, skip_planner=False
            ):
                yield event

    async def confirm_action(self, confirm_id: str) -> AsyncGenerator[dict, None]:
        """Run a previously gated tool call now that the operator approved it.
        Works the same whether the call came from Quick mode or a swarm worker —
        both store the same (skill, action, params) tuple."""
        if confirm_id not in self.pending_confirms:
            yield {"type": "error", "data": "Confirmation ID not found or already used."}
            return

        skill_name, action, params = self.pending_confirms.pop(confirm_id)
        yield {"type": "tool_call", "data": {"skill": skill_name, "action": action, "params": params, "confirmed": True}}

        result      = await self.registry.execute(skill_name, action, params, confirmed=True)
        result_dict = result.to_dict()
        yield {"type": "tool_result", "data": {"skill": skill_name, "action": action, "params": params, **result_dict}}

        ctx = f"[CONFIRMED TOOL RESULT: {skill_name}.{action}]\n{json.dumps(result_dict, indent=2, default=str)}"
        self._append("user", ctx)

        summary = ""
        confirm_sp = build_system_prompt(persona=self.persona)
        async for token in self.llm.chat_stream(self.conversation, system=confirm_sp):
            summary += token
            yield {"type": "token", "data": token}

        self._append("assistant", summary or "✓")
        _memory.save_turn(self.user_id, "assistant", summary or "✓")
        yield {"type": "done", "data": {}}

    # ── Quick mode (react=False) — identical semantics to the old single-cycle ──

    async def _single_cycle(self, user_message: str, auto_confirm: bool, system_prompt: str) -> AsyncGenerator[dict, None]:
        self._append("user", user_message)

        full_response = ""
        tool_calls_found: list = []
        yield {"type": "token", "data": ""}

        async for token in self.llm.chat_stream(self.conversation, system=system_prompt):
            full_response += token
            yield {"type": "token", "data": token}
            for call in self._extract_tool_calls(full_response):
                if call not in tool_calls_found:
                    tool_calls_found.append(call)

        self._append("assistant", full_response)
        _memory.save_conversation_block(self.user_id, user_message, full_response)

        for call in tool_calls_found:
            skill_name = call.get("skill", "")
            action     = call.get("action", "")
            params     = call.get("params", {})

            yield {"type": "tool_call", "data": {"skill": skill_name, "action": action, "params": params}}
            result = await self.registry.execute(skill_name, action, params, confirmed=auto_confirm)

            if result.requires_confirm and not auto_confirm:
                self._confirm_counter += 1
                confirm_id = f"{skill_name}:{action}:{self._confirm_counter}"
                self.pending_confirms[confirm_id] = (skill_name, action, params)
                yield {"type": "confirm_needed", "data": {
                    "confirm_id": confirm_id, "prompt": result.confirm_prompt,
                    "skill": skill_name, "action": action,
                }}
                yield {"type": "done", "data": {}}
                return

            result_dict = result.to_dict()
            yield {"type": "tool_result", "data": {"skill": skill_name, "action": action, "params": params, **result_dict}}
            ctx = f"[TOOL RESULT: {skill_name}.{action}]\n{json.dumps(result_dict, indent=2, default=str)}"
            self._append("user", ctx)

            summary = ""
            async for token in self.llm.chat_stream(self.conversation, system=system_prompt):
                summary += token
                yield {"type": "token", "data": token}
            self._append("assistant", summary or "✓")
            _memory.save_turn(self.user_id, "assistant", summary or "✓")

        yield {"type": "done", "data": {}}

    # ── Layer 1: Classifier ───────────────────────────────────────────────────

    async def _classify(self, message: str) -> str:
        try:
            raw  = await self.llm.chat([{"role": "user", "content": message}], system=CLASSIFIER_SYSTEM_PROMPT)
            data = json.loads(raw.strip())
            cat  = data.get("category", "chat")
            return cat if cat in ("chat", "task") else "chat"
        except Exception:
            lower = message.lower()
            return "task" if any(k in lower for k in CHAT_HEURISTIC_TASK_KEYWORDS) else "chat"

    # ── Layer 2a: Chat path — direct streamed reply, no tools ───────────────────

    async def _handle_chat_stream(self, user_message: str, system_prompt: str) -> AsyncGenerator[dict, None]:
        self._append("user", user_message)
        full_response = ""
        async for token in self.llm.chat_stream(self.conversation, system=system_prompt):
            full_response += token
            yield {"type": "token", "data": token}
        self._append("assistant", full_response)
        _memory.save_conversation_block(self.user_id, user_message, full_response)
        yield {"type": "done", "data": {"react_complete": True, "category": "chat"}}

    # ── Layer 2b: Planner ─────────────────────────────────────────────────────

    async def _generate_plan(self, message: str) -> list:
        system = PLANNER_SYSTEM_PROMPT.replace("__MAX_PARALLEL__", str(self.max_parallel))
        try:
            raw   = await self.llm.chat([{"role": "user", "content": f"Plan this: {message}"}], system=system)
            clean = raw.strip()
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
            return tasks or [Task(task_id="TSK-001", goal=message, context={"user_id": self.user_id},
                                   validation_criteria="Request fulfilled")]
        except Exception as exc:
            logger.error("[brain_planner_failed] err=%s", exc)
            return [Task(task_id="TSK-001", goal=message, context={"user_id": self.user_id},
                         validation_criteria="Request fulfilled")]

    # ── Task pipeline: plan (optional) → swarm → consolidate → summarize ───────

    async def _run_task_pipeline(self, message: str, auto_confirm: bool, persona: str,
                                  re_act_max_loop: int, skip_planner: bool) -> AsyncGenerator[dict, None]:
        self._append("user", message)
        _memory.save_turn(self.user_id, "user", message)

        if skip_planner:
            tasks = [Task(task_id="TSK-001", goal=message, context={"user_id": self.user_id},
                          validation_criteria="Request fulfilled")]
            yield {"type": "react_status", "data": {
                "iteration": 0, "max": re_act_max_loop,
                "phase": f"deep task — 1 worker, up to {re_act_max_loop} iterations", "healing": False,
            }}
        else:
            yield {"type": "react_status", "data": {"iteration": 0, "max": 0, "phase": "planning", "healing": False}}
            tasks = await self._generate_plan(message)
            yield {"type": "react_status", "data": {
                "iteration": 0, "max": 0, "phase": f"plan ready — {len(tasks)} task(s)", "healing": False,
            }}

        async for event in self._run_swarm_streaming(tasks, auto_confirm, persona, re_act_max_loop):
            yield event

        self.task_registry.extend(tasks)
        report = self._consolidate(tasks)

        yield {"type": "react_status", "data": {"iteration": 0, "max": 0, "phase": "consolidating", "healing": False}}

        summary = ""
        summary_conv = [{"role": "user", "content": f"Original request: {message}\n\nExecution report:\n{report}"}]
        async for token in self.llm.chat_stream(summary_conv, system=SUMMARY_SYSTEM_PROMPT):
            summary += token
            yield {"type": "token", "data": token}

        self._append("assistant", summary or report)
        _memory.save_turn(self.user_id, "assistant", summary or report)

        needs_attention = any(t.status == "FAILED" and t.error == "requires_confirmation" for t in tasks)
        yield {"type": "done", "data": {
            "react_complete":  not needs_attention,
            "iterations":      max((t.iterations_used for t in tasks), default=0),
            "tasks":           [self._task_to_dict(t) for t in tasks],
        }}

    # ── Layer 3: Swarm execution (asyncio, dependency-ordered batches) ──────────

    async def _run_swarm_streaming(self, tasks: list, auto_confirm: bool, persona: str,
                                    re_act_max_loop: int) -> AsyncGenerator[dict, None]:
        task_map  = {t.task_id: t for t in tasks}
        completed: set = set()
        all_ids        = set(task_map.keys())
        sem = asyncio.Semaphore(max(1, self.max_parallel))

        while completed != all_ids:
            ready = [t for t in tasks if t.status == "PENDING" and all(d in completed for d in t.depends_on)]
            if not ready:
                remaining = [t for t in tasks if t.status == "PENDING"]
                if remaining:
                    logger.warning("[brain_swarm] dependency deadlock — skipping %d task(s)", len(remaining))
                    for t in remaining:
                        t.status, t.error = "SKIPPED", "Dependency deadlock"
                break

            yield {"type": "react_status", "data": {
                "iteration": 0, "max": re_act_max_loop,
                "phase": f"swarm — {len(ready)} task(s) running (cap {re_act_max_loop} iter/worker)",
                "healing": False,
            }}

            queue: asyncio.Queue = asyncio.Queue()

            async def _bounded_run(task: Task):
                try:
                    async with sem:
                        await asyncio.wait_for(
                            self._run_worker(task, task_map, auto_confirm, persona, re_act_max_loop, queue),
                            timeout=SWARM_WORKER_TIMEOUT_S,
                        )
                except asyncio.TimeoutError:
                    task.status, task.error = "FAILED", f"Worker timeout ({SWARM_WORKER_TIMEOUT_S}s)"
                    logger.error("[brain_swarm] worker timeout task_id=%s", task.task_id)
                except Exception as exc:
                    task.status, task.error = "FAILED", str(exc)
                    logger.error("[brain_swarm] worker error task_id=%s err=%s", task.task_id, exc)
                finally:
                    await queue.put(None)   # sentinel — guarantees the drain loop below never hangs

            runner_tasks = [asyncio.create_task(_bounded_run(t)) for t in ready]

            remaining_workers = len(runner_tasks)
            while remaining_workers > 0:
                item = await queue.get()
                if item is None:
                    remaining_workers -= 1
                    continue
                yield item   # live tool_call / tool_result / confirm_needed from inside a worker

            await asyncio.gather(*runner_tasks, return_exceptions=True)
            for t in ready:
                completed.add(t.task_id)

    async def _run_worker(self, task: Task, task_map: dict, auto_confirm: bool, persona: str,
                           re_act_max_loop: int, queue: asyncio.Queue):
        """Isolated ephemeral worker. No shared state with the main conversation or
        other workers — only `context` flows down, only `result`/`status` flows up."""
        task.status = "RUNNING"
        logger.info("[brain_swarm] start task_id=%s goal=%.60s", task.task_id, task.goal)

        dep_results = {d: task_map[d].result for d in task.depends_on if d in task_map}
        base_prompt = build_system_prompt(persona=persona)
        system_prompt = (
            f"You are a focused task executor inside an isolated swarm worker.\n\n"
            f"TASK ID    : {task.task_id}\n"
            f"GOAL       : {task.goal}\n"
            f"CONTEXT    : {json.dumps(task.context, indent=2, default=str)}\n"
            f"VALIDATION : {task.validation_criteria}\n\n"
            f"Execute the task using TOOL_CALL blocks when needed.\n"
            f"When the goal is achieved, write TASK_COMPLETE and your result summary.\n"
            f"You have maximum {re_act_max_loop} reasoning iterations.\n"
            f"Be concise — the orchestrator only needs your final result, not your thinking process.\n\n"
            f"{base_prompt}"
        )
        local_conversation: list = [{
            "role": "user",
            "content": (
                f"Execute this task:\n\nGOAL: {task.goal}\n\n"
                f"CONTEXT: {json.dumps({**task.context, 'dependency_results': dep_results}, default=str)}\n\n"
                f"VALIDATION: {task.validation_criteria}"
            ),
        }]

        for iteration in range(1, re_act_max_loop + 1):
            task.iterations_used = iteration
            try:
                response = await self.llm.chat(local_conversation, system=system_prompt)
            except Exception as exc:
                local_conversation.append({"role": "user", "content": f"[ERROR]\n{exc}\nAdapt and continue."})
                continue
            local_conversation.append({"role": "assistant", "content": response})

            # Surface the worker's own reasoning text. This is what carries any
            # "Phase N — ..." narration (e.g. /api/evolve's 9-phase protocol) —
            # tool_call/tool_result only carry skill/action/output, never the
            # model's own commentary, and the worker calls the LLM non-streamed
            # (one full response per iteration, not token-by-token) so this is
            # emitted as a single chunk per iteration rather than incrementally.
            await queue.put({"type": "token", "data": f"\n── [{task.task_id} · iter {iteration}/{re_act_max_loop}] ──\n{response}\n"})

            if self._is_complete(response):
                task.result, task.status = self._extract_result(response), "COMPLETED"
                logger.info("[brain_swarm] complete task_id=%s iter=%d", task.task_id, iteration)
                return

            calls = self._extract_tool_calls(response)
            if not calls:
                task.result, task.status = response.strip(), "COMPLETED"
                logger.info("[brain_swarm] done (no tools) task_id=%s iter=%d", task.task_id, iteration)
                return

            observations: list = []
            for call in calls:
                skill_name = call.get("skill", "")
                action     = call.get("action", "")
                params     = call.get("params", {})
                await queue.put({"type": "tool_call", "data": {
                    "skill": skill_name, "action": action, "params": params, "task_id": task.task_id,
                }})
                try:
                    result = await self.registry.execute(skill_name, action, params, confirmed=auto_confirm)
                except Exception as tool_exc:
                    observations.append(f"[TOOL ✗ {skill_name}.{action} EXCEPTION]\n{tool_exc}")
                    continue

                if result.requires_confirm and not auto_confirm:
                    # Swarm workers don't pause-and-wait for a human (would block other
                    # concurrent workers indefinitely) — surface confirm_needed for
                    # visibility/logging, skip the action, and let the worker adapt or
                    # report it. The operator can run it via confirm_action() afterwards.
                    self._confirm_counter += 1
                    confirm_id = f"{skill_name}:{action}:{self._confirm_counter}"
                    self.pending_confirms[confirm_id] = (skill_name, action, params)
                    await queue.put({"type": "confirm_needed", "data": {
                        "confirm_id": confirm_id, "prompt": result.confirm_prompt,
                        "skill": skill_name, "action": action, "task_id": task.task_id,
                    }})
                    observations.append(
                        f"[TOOL ⏸ {skill_name}.{action} NEEDS OPERATOR CONFIRMATION — confirm_id={confirm_id}]\n"
                        f"This action requires explicit approval and can't run unattended. "
                        f"Skip it, try a non-destructive alternative, or note it in your result."
                    )
                    continue

                result_dict = result.to_dict()
                await queue.put({"type": "tool_result", "data": {
                    "skill": skill_name, "action": action, "params": params, "task_id": task.task_id, **result_dict,
                }})
                if result_dict.get("success"):
                    observations.append(f"[TOOL ✓ {skill_name}.{action}]\n{json.dumps(result_dict.get('output'), indent=2, default=str)}")
                else:
                    observations.append(
                        f"[TOOL ✗ {skill_name}.{action} FAILED]\nError: {result_dict.get('error')}\n"
                        f"Diagnose and adjust approach."
                    )
            local_conversation.append({"role": "user", "content": "\n\n".join(observations)})

        last_resp = local_conversation[-1].get("content", "") if local_conversation else "No result"
        task.result = f"[MAX_ITERATIONS_REACHED after {re_act_max_loop} loops]\n{last_resp}"
        task.status = "COMPLETED"
        logger.warning("[brain_swarm] max_iter task_id=%s", task.task_id)

    # ── Layer 4: Consolidation ───────────────────────────────────────────────────

    def _consolidate(self, tasks: list) -> str:
        lines = ["# Swarm Execution Report\n"]
        for t in tasks:
            icon = {"COMPLETED": "✓", "FAILED": "✗", "SKIPPED": "⊘"}.get(t.status, "?")
            lines.append(f"## [{icon}] {t.task_id} — {t.goal}")
            lines.append(f"**Status:** {t.status}  |  **Iterations:** {t.iterations_used}")
            if t.result:
                lines.append(f"**Result:**\n{t.result}")
            if t.error:
                lines.append(f"**Error:** {t.error}")
            lines.append("")
        return "\n".join(lines)

    # ── Shared helpers (same parsing contract as the old Agent) ──────────────────

    def _is_complete(self, text: str) -> bool:
        if "TASK_COMPLETE" in text or "task_complete" in text:
            return True
        lower = text.lower().strip()
        return any(signal in lower for signal in COMPLETION_SIGNALS)

    def _extract_result(self, text: str) -> str:
        for marker in ("TASK_COMPLETE", "task_complete"):
            if marker in text:
                idx = text.find(marker)
                return text[idx + len(marker):].strip().lstrip(":").strip()
        return text.strip()

    def _extract_tool_calls(self, text: str) -> list:
        """Brace-counting TOOL_CALL: {...} parser — identical contract to the old Agent."""
        calls: list = []
        seen:  set  = set()
        start = 0
        while True:
            idx = text.find(TOOL_CALL_MARKER, start)
            if idx == -1:
                break
            brace_start = text.find("{", idx + len(TOOL_CALL_MARKER))
            if brace_start == -1:
                break
            depth, i = 0, brace_start
            while i < len(text):
                ch = text[i]
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        json_str = text[brace_start:i + 1]
                        try:
                            call = json.loads(json_str)
                            if "skill" in call and "action" in call:
                                key = f"{call['skill']}:{call['action']}:{json.dumps(call.get('params', {}), sort_keys=True)}"
                                if key not in seen:
                                    seen.add(key)
                                    calls.append(call)
                        except json.JSONDecodeError:
                            pass
                        break
                i += 1
            start = idx + 1
        return calls

    def _task_to_dict(self, t: Task) -> dict:
        return {
            "task_id": t.task_id, "goal": t.goal, "status": t.status, "result": t.result,
            "error": t.error, "iterations_used": t.iterations_used,
            "validation_criteria": t.validation_criteria, "depends_on": t.depends_on,
        }