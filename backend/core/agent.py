"""
Agent Core - Orchestrates LLM + Skills.
Handles tool-call parsing, confirmation flow, streaming responses,
and native ReAct (Reason+Act) loop with self-healing.

version: 2.0.0
changelog:
  1.0.0 - Initial agent with single Reason→Act cycle
  2.0.0 - Native ReAct loop: multi-iteration Reason→Act→Observe inside one
           chat_stream call. Self-healing on tool failure. react_status events.
           auto_confirm flag. Max-iteration safety cap.
"""
import os
import json
import logging
from typing import AsyncGenerator
from core.llm_router import LLMRouter, LLMConfig
from core.skill_registry import SkillRegistry

logger = logging.getLogger("agent.core")

# ── Max ReAct iterations — safety cap to prevent runaway loops ────────────────
REACT_MAX_ITERATIONS = 30

# ── Skill Manifest Loader ─────────────────────────────────────────────
MANIFEST_PATH = os.path.join(os.path.dirname(__file__), ".", "skills_manifest.json")


def _load_skills_manifest() -> str:
    """Load the static skills manifest and return a formatted prompt section."""
    try:
        with open(MANIFEST_PATH, "r") as f:
            manifest = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning("skills_manifest.json not found — falling back to minimal skill list")
        return ""

    lines = []
    # SecOps skills
    lines.append("### SecOps Skills (Security Operations)")
    for s in manifest.get("secops_skills", []):
        actions = ", ".join(s.get("actions", ["run"]))
        lines.append(f"- **{s['name']}**: {s['description']} (actions: {actions})")

    lines.append("")
    # System skills
    lines.append("### System Skills")
    for s in manifest.get("system_skills", []):
        actions = ", ".join(s.get("actions", []))
        lines.append(f"- **{s['name']}**: {s['description']} (actions: {actions})")

    return "\n".join(lines)

_SKILLS_MANIFEST_SECTION = _load_skills_manifest()

# ── Keywords that signal the LLM considers the task complete ─────────────────
COMPLETION_SIGNALS = [
    "task complete", "task is complete", "task completed",
    "all done", "finished", "i have completed", "successfully completed",
    "the task is done", "work is complete", "done.",
]


skills_section = _SKILLS_MANIFEST_SECTION if _SKILLS_MANIFEST_SECTION else """
### 1. filesystem
Full host filesystem access.
Actions: read_file, write_file, list_dir, delete, move, mkdir, search_files, stat
Usage: SKILL:filesystem ACTION:read_file PARAMS:{"path": "/etc/hosts"}

### 2. os_execution
Full OS control — run commands, manage processes.
Actions: run_command, list_processes, kill_process, send_signal, system_info, env_vars
Usage: SKILL:os_execution ACTION:run_command PARAMS:{"command": "ls -la", "cwd": "/tmp"}

### 3. cbd_architect
CBD v2.2 methodology enforcer — Phase -1 clarification, Phase 0 Experienced lookup,
Phase I blueprint, Phase II atomic implementation, Phase III knowledge capture.
Actions:
  analyze_request, generate_blueprint, implement_component, validate_component,
  validate_blueprint, version_read,
  experienced_lookup, experienced_capture, experienced_promote,
  experienced_search, experienced_rebuild_index,
  get_template, get_skills_registry
Usage: SKILL:cbd_architect ACTION:analyze_request PARAMS:{"request": "Build a REST API..."}
Usage: SKILL:cbd_architect ACTION:experienced_lookup PARAMS:{"task_context": {"symptom_observed": "error message here"}}

### 4. file_streamer (Default skill for handling large file writes that exceed LLM token limits)
Handles large file payloads by allowing chunked, segmented appending to bypass LLM max_token generation constraints. Essential for writing files that exceed single-turn output limits.
Actions:
    start_file, append_chunk, finalize_file
Usage: SKILL:file_streamer ACTION:start_file PARAMS:{"filepath": "./output/large_script.py", "overwrite": true}
Usage: SKILL:file_streamer ACTION:append_chunk PARAMS:{"filepath": "./output/large_script.py", "content": "def main():\n    print('Part 1 of code...')"}
Usage: SKILL:file_streamer ACTION:finalize_file PARAMS:{"filepath": "./output/large_script.py"}

"""



AGENT_SYSTEM_PROMPT = """You are Jarvis a powerful AI agent with access to the following skills:

## Available Skills & Actions:

""" + skills_section + """




## Tool Call Format:
When you need to use a skill, output EXACTLY this format on its own line:
TOOL_CALL: {"skill": "skill_name", "action": "action_name", "params": {...}}

## Core Behavioral Rules:
1. NEVER make assumptions about ambiguous requests. Ask for clarification first.
2. For CBD requests, ALWAYS use the cbd_architect skill — never freehand architecture.
3. Before any destructive filesystem or OS action, warn the user what will happen.
4. If a skill returns requires_confirm=true, present the confirmation prompt to the user clearly.
5. Think step by step. Show your reasoning before tool calls.
6. After receiving tool results, summarize what happened and what's next.

## ReAct Mode Rules (when operating in ReAct loop):
- After each tool result, reason about the outcome before deciding next action.
- On tool failure: diagnose the root cause, adjust your approach, and retry with a corrected call.
- When the task is fully complete, end your response with: TASK_COMPLETE
- Do NOT output TASK_COMPLETE unless ALL objectives have been achieved and verified.
- Each iteration should make measurable progress toward the goal.

## Safety:
- Destructive actions (file writes, deletes, shell commands, process kills) REQUIRE user confirmation
  unless auto_confirm mode is active.
- Always show the exact command/path before executing.
- Never chain destructive actions without confirmation between each (unless auto_confirm is on).
"""


class Agent:
    def __init__(self, config: LLMConfig, registry: SkillRegistry):
        self.llm = LLMRouter(config)
        self.registry = registry
        self.conversation: list = []
        self.pending_confirms: dict = {}  # confirm_id -> (skill, action, params)
        self._confirm_counter: int = 0

    def reset(self):
        self.conversation = []
        self.pending_confirms = {}
        self._confirm_counter = 0

    # ── Public entry points ───────────────────────────────────────────────────

    async def chat_stream(
        self,
        user_message: str,
        react: bool = False,
        auto_confirm: bool = False,
    ) -> AsyncGenerator[dict, None]:
        """
        Main agent entry point.

        react=False  → single Reason→Act cycle (original behaviour).
        react=True   → full ReAct loop: Reason→Act→Observe, repeated until
                       TASK_COMPLETE signal or REACT_MAX_ITERATIONS reached.
                       Self-heals on tool failure by injecting error observations.
        auto_confirm → skip confirmation gate for destructive actions.
        """
        if react:
            async for event in self._react_loop(user_message, auto_confirm):
                yield event
        else:
            async for event in self._single_cycle(user_message, auto_confirm):
                yield event

    async def confirm_action(self, confirm_id: str) -> AsyncGenerator[dict, None]:
        """Execute a previously-gated destructive action after user confirmation."""
        if confirm_id not in self.pending_confirms:
            yield {"type": "error", "data": "Confirmation ID not found or already used."}
            return

        skill_name, action, params = self.pending_confirms.pop(confirm_id)
        yield {"type": "tool_call", "data": {
            "skill": skill_name, "action": action, "params": params, "confirmed": True,
        }}

        result = await self.registry.execute(skill_name, action, params, confirmed=True)
        result_dict = result.to_dict()
        yield {"type": "tool_result", "data": result_dict}

        tool_context = f"[CONFIRMED TOOL RESULT: {skill_name}.{action}]\n{json.dumps(result_dict, indent=2)}"
        self.conversation.append({"role": "user", "content": tool_context})

        summary = ""
        async for token in self.llm.chat_stream(self.conversation, system=AGENT_SYSTEM_PROMPT):
            summary += token
            yield {"type": "token", "data": token}

        self.conversation.append({"role": "assistant", "content": summary or "✓"})
        yield {"type": "done", "data": {}}

    # ── Single cycle (original behaviour) ────────────────────────────────────

    async def _single_cycle(
        self, user_message: str, auto_confirm: bool = False
    ) -> AsyncGenerator[dict, None]:
        """One Reason→Act cycle: LLM → tool calls → summary."""
        self.conversation.append({"role": "user", "content": user_message})

        full_response, tool_calls_found = "", []
        yield {"type": "token", "data": ""}

        async for token in self.llm.chat_stream(self.conversation, system=AGENT_SYSTEM_PROMPT):
            full_response += token
            yield {"type": "token", "data": token}
            for call in self._extract_tool_calls(full_response):
                if call not in tool_calls_found:
                    tool_calls_found.append(call)

        self.conversation.append({"role": "assistant", "content": full_response})

        if tool_calls_found:
            for call in tool_calls_found:
                async for event in self._execute_call(call, auto_confirm):
                    yield event

                    # After a confirm_needed the loop pauses — frontend drives next step
                    if event.get("type") == "confirm_needed":
                        yield {"type": "done", "data": {}}
                        return

                # Feed result back and get summary
                last_result = self.conversation[-1]  # already appended by _execute_call
                summary = ""
                async for token in self.llm.chat_stream(self.conversation, system=AGENT_SYSTEM_PROMPT):
                    summary += token
                    yield {"type": "token", "data": token}
                self.conversation.append({"role": "assistant", "content": summary or "✓"})

        yield {"type": "done", "data": {}}

    # ── ReAct loop ────────────────────────────────────────────────────────────

    async def _react_loop(
        self, user_message: str, auto_confirm: bool = False
    ) -> AsyncGenerator[dict, None]:
        """
        Full ReAct loop.

        Each iteration:
          1. Reason  — LLM generates a response (may include TOOL_CALLs)
          2. Act     — execute any tool calls found
          3. Observe — inject results back as observations
          4. Repeat  — until TASK_COMPLETE or no tool calls or max iterations

        Self-healing: on tool failure, observation includes the error and an
        explicit instruction to diagnose and correct before retrying.
        """
        self.conversation.append({"role": "user", "content": user_message})

        for iteration in range(1, REACT_MAX_ITERATIONS + 1):
            yield {"type": "react_status", "data": {
                "iteration": iteration,
                "max": REACT_MAX_ITERATIONS,
                "phase": "reasoning",
                "healing": False,
            }}

            # ── Reason ────────────────────────────────────────────────────────
            full_response, tool_calls_found = "", []
            yield {"type": "token", "data": ""}

            async for token in self.llm.chat_stream(self.conversation, system=AGENT_SYSTEM_PROMPT):
                full_response += token
                yield {"type": "token", "data": token}
                for call in self._extract_tool_calls(full_response):
                    if call not in tool_calls_found:
                        tool_calls_found.append(call)

            self.conversation.append({"role": "assistant", "content": full_response})

            # ── Check for task completion signal ──────────────────────────────
            if self._is_complete(full_response):
                yield {"type": "react_status", "data": {
                    "iteration": iteration,
                    "max": REACT_MAX_ITERATIONS,
                    "phase": "complete",
                    "healing": False,
                }}
                yield {"type": "done", "data": {"react_complete": True, "iterations": iteration}}
                return

            # ── No tool calls → task complete or agent is asking for input ────
            if not tool_calls_found:
                yield {"type": "react_status", "data": {
                    "iteration": iteration,
                    "max": REACT_MAX_ITERATIONS,
                    "phase": "no_tool_calls — awaiting input or task complete",
                    "healing": False,
                }}
                yield {"type": "done", "data": {"react_complete": False, "iterations": iteration}}
                return

            # ── Act + Observe ─────────────────────────────────────────────────
            yield {"type": "react_status", "data": {
                "iteration": iteration,
                "max": REACT_MAX_ITERATIONS,
                "phase": f"acting — {len(tool_calls_found)} tool call(s)",
                "healing": False,
            }}

            observations = []
            hit_confirm = False

            for call in tool_calls_found:
                skill_name = call.get("skill")
                action = call.get("action")
                params = call.get("params", {})

                yield {"type": "tool_call", "data": {
                    "skill": skill_name, "action": action, "params": params,
                }}

                result = await self.registry.execute(
                    skill_name, action, params, confirmed=auto_confirm
                )

                if result.requires_confirm and not auto_confirm:
                    # Pause loop — hand control back to user
                    self._confirm_counter += 1
                    confirm_id = f"{skill_name}:{action}:{self._confirm_counter}"
                    self.pending_confirms[confirm_id] = (skill_name, action, params)
                    yield {"type": "confirm_needed", "data": {
                        "confirm_id": confirm_id,
                        "prompt": result.confirm_prompt,
                        "skill": skill_name,
                        "action": action,
                    }}
                    hit_confirm = True
                    break  # exit tool loop — resume after user confirms

                result_dict = result.to_dict()
                yield {"type": "tool_result", "data": result_dict}

                if result_dict.get("success"):
                    obs = (
                        f"[OBSERVATION — {skill_name}.{action} ✓]\n"
                        f"{json.dumps(result_dict.get('output'), indent=2)}"
                    )
                else:
                    # Self-healing: rich error observation
                    obs = (
                        f"[OBSERVATION — {skill_name}.{action} ✗ FAILED]\n"
                        f"Error: {result_dict.get('error')}\n"
                        f"Self-healing instruction: Diagnose why {action} on {skill_name} "
                        f"failed with the above error. Adjust your parameters or approach "
                        f"and retry in the next iteration."
                    )
                    yield {"type": "react_status", "data": {
                        "iteration": iteration,
                        "max": REACT_MAX_ITERATIONS,
                        "phase": f"tool failure — self-healing on next iter",
                        "healing": True,
                    }}

                observations.append(obs)

            if hit_confirm:
                # Yield done so the frontend can re-engage after confirmation
                yield {"type": "done", "data": {"react_paused": True, "iterations": iteration}}
                return

            # Inject all observations as a single user turn
            if observations:
                combined = "\n\n".join(observations)
                self.conversation.append({"role": "user", "content": combined})

        # ── Max iterations reached ────────────────────────────────────────────
        yield {"type": "react_status", "data": {
            "iteration": REACT_MAX_ITERATIONS,
            "max": REACT_MAX_ITERATIONS,
            "phase": f"max iterations ({REACT_MAX_ITERATIONS}) reached",
            "healing": False,
        }}
        yield {"type": "done", "data": {
            "react_complete": False,
            "iterations": REACT_MAX_ITERATIONS,
            "reason": "max_iterations",
        }}

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _execute_call(
        self, call: dict, auto_confirm: bool
    ) -> AsyncGenerator[dict, None]:
        """Execute one tool call, yielding tool_call / confirm_needed / tool_result."""
        skill_name = call.get("skill")
        action = call.get("action")
        params = call.get("params", {})

        yield {"type": "tool_call", "data": {"skill": skill_name, "action": action, "params": params}}

        result = await self.registry.execute(
            skill_name, action, params, confirmed=auto_confirm
        )

        if result.requires_confirm and not auto_confirm:
            self._confirm_counter += 1
            confirm_id = f"{skill_name}:{action}:{self._confirm_counter}"
            self.pending_confirms[confirm_id] = (skill_name, action, params)
            yield {"type": "confirm_needed", "data": {
                "confirm_id": confirm_id,
                "prompt": result.confirm_prompt,
                "skill": skill_name,
                "action": action,
            }}
        else:
            result_dict = result.to_dict()
            yield {"type": "tool_result", "data": result_dict}
            tool_context = (
                f"[TOOL RESULT: {skill_name}.{action}]\n"
                f"{json.dumps(result_dict, indent=2)}"
            )
            self.conversation.append({"role": "user", "content": tool_context})

    def _is_complete(self, text: str) -> bool:
        """Return True if the LLM signalled task completion."""
        lower = text.lower()
        if "task_complete" in text or "TASK_COMPLETE" in text:
            return True
        return any(signal in lower for signal in COMPLETION_SIGNALS)

    def _extract_tool_calls(self, text: str) -> list:
        """
        Parse TOOL_CALL: {...} blocks from LLM output.
        Uses brace-counting instead of regex — handles nested JSON correctly.
        """
        calls = []
        marker = "TOOL_CALL:"
        start = 0
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
                        json_str = text[brace_start: i + 1]
                        try:
                            call = json.loads(json_str)
                            if "skill" in call and "action" in call:
                                calls.append(call)
                        except json.JSONDecodeError:
                            pass
                        break
                i += 1
            start = idx + 1
        return calls


