"""
Agent Core - Orchestrates LLM + Skills.
Handles tool-call parsing, confirmation flow, streaming responses,
and native ReAct (Reason+Act) loop with self-healing.

version: 3.0.0
changelog:
  1.0.0 - Initial agent with single Reason→Act cycle
  2.0.0 - Native ReAct loop: multi-iteration Reason→Act→Observe inside one
           chat_stream call. Self-healing on tool failure. react_status events.
           auto_confirm flag. Max-iteration safety cap.
  3.0.0 - System prompt extracted to prompt_builder.py.
           MemoryManager integration (auto-save history, on-demand retrieval).
           File attachment support (text, PDF, image) injected into user messages.
"""
import os
import json
import base64
import logging
from typing import AsyncGenerator, Optional
from core.llm_router import LLMRouter, LLMConfig
from core.skill_registry import SkillRegistry
from core.prompt_builder import build_system_prompt, AGENT_SYSTEM_PROMPT
from core.memory_manager import MemoryManager, detect_retrieval_request

logger = logging.getLogger("agent.core")

# ── Max ReAct iterations — safety cap to prevent runaway loops ─────────────────
REACT_MAX_ITERATIONS = 30

# ── Completion signals ─────────────────────────────────────────────────────────
COMPLETION_SIGNALS = [
    "task complete", "task is complete", "task completed",
    "all done", "finished", "i have completed", "successfully completed",
    "the task is done", "work is complete", "done.",
]

# ── Singleton memory manager ───────────────────────────────────────────────────
_memory = MemoryManager()


def _format_attachment(attachment: dict) -> str:
    """
    Convert an uploaded file dict into an inline block for the LLM message.

    Supported attachment dict keys:
      name     : original filename
      mime     : MIME type  (e.g. "text/plain", "image/png", "application/pdf")
      size     : byte size (int)
      content  : raw bytes  OR
      text     : decoded text string (for text/* types)
      b64      : base64-encoded string (for binary types)
    """
    name = attachment.get("name", "file")
    mime = attachment.get("mime", "application/octet-stream")
    size = attachment.get("size", 0)

    if attachment.get("text"):
        body = attachment["text"]
        # Truncate very large text files
        if len(body) > 8000:
            body = body[:8000] + f"\n… [truncated — {len(attachment['text'])} chars total]"
        return (
            f"[FILE: {name} | type: {mime} | size: {size} bytes]\n"
            f"{body}\n"
            f"[/FILE]"
        )
    elif attachment.get("b64"):
        # For images/PDFs we include base64; the LLM may or may not use it
        b64_snippet = attachment["b64"][:200] + "…" if len(attachment["b64"]) > 200 else attachment["b64"]
        return (
            f"[FILE: {name} | type: {mime} | size: {size} bytes | encoding: base64]\n"
            f"{b64_snippet}\n"
            f"[/FILE]\n"
            f"(Full base64 content available — treat as {mime} file)"
        )
    else:
        return f"[FILE: {name} | type: {mime} | size: {size} bytes | content: unavailable]"


class Agent:
    def __init__(self, config: LLMConfig, registry: SkillRegistry, user_id: str = "default"):
        self.llm = LLMRouter(config)
        self.registry = registry
        self.conversation: list = []
        self.pending_confirms: dict = {}   # confirm_id → (skill, action, params)
        self._confirm_counter: int = 0
        self.user_id: str = user_id

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
        attachments: Optional[list] = None,
    ) -> AsyncGenerator[dict, None]:
        """
        Main agent entry point.

        react=False  → single Reason→Act cycle.
        react=True   → full ReAct loop: Reason→Act→Observe, repeated until
                       TASK_COMPLETE signal or REACT_MAX_ITERATIONS reached.
        auto_confirm → skip confirmation gate for destructive actions.
        attachments  → list of file dicts (name, mime, size, text|b64).
        """
        # ── Build the enriched user message ───────────────────────────────────
        enriched_message = user_message

        # Inject file attachments
        if attachments:
            att_blocks = [_format_attachment(a) for a in attachments]
            enriched_message = "\n\n".join(att_blocks) + "\n\n" + user_message

        # ── Check for memory retrieval request ─────────────────────────────────
        n = detect_retrieval_request(user_message)
        system_prompt = AGENT_SYSTEM_PROMPT
        if n is not None:
            history = _memory.retrieve_last_n(self.user_id, n)
            system_prompt = build_system_prompt(memory_context=history)
            logger.info("Memory retrieval injected for user %s (%d turns)", self.user_id, n)

        # ── Dispatch ───────────────────────────────────────────────────────────
        if react:
            async for event in self._react_loop(enriched_message, auto_confirm, system_prompt):
                yield event
        else:
            async for event in self._single_cycle(enriched_message, auto_confirm, system_prompt):
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
        self,
        user_message: str,
        auto_confirm: bool = False,
        system_prompt: str = None,
    ) -> AsyncGenerator[dict, None]:
        """One Reason→Act cycle: LLM → tool calls → summary."""
        sp = system_prompt or AGENT_SYSTEM_PROMPT
        self.conversation.append({"role": "user", "content": user_message})

        full_response, tool_calls_found = "", []
        yield {"type": "token", "data": ""}

        async for token in self.llm.chat_stream(self.conversation, system=sp):
            full_response += token
            yield {"type": "token", "data": token}
            for call in self._extract_tool_calls(full_response):
                if call not in tool_calls_found:
                    tool_calls_found.append(call)

        self.conversation.append({"role": "assistant", "content": full_response})

        # ── Persist to memory ──────────────────────────────────────────────────
        _memory.save_conversation_block(self.user_id, user_message, full_response)

        if tool_calls_found:
            for call in tool_calls_found:
                async for event in self._execute_call(call, auto_confirm, sp):
                    yield event
                    if event.get("type") == "confirm_needed":
                        yield {"type": "done", "data": {}}
                        return

                summary = ""
                async for token in self.llm.chat_stream(self.conversation, system=sp):
                    summary += token
                    yield {"type": "token", "data": token}
                self.conversation.append({"role": "assistant", "content": summary or "✓"})
                _memory.save_turn(self.user_id, "assistant", summary or "✓")

        yield {"type": "done", "data": {}}

    # ── ReAct loop ────────────────────────────────────────────────────────────

    async def _react_loop(
        self,
        user_message: str,
        auto_confirm: bool = False,
        system_prompt: str = None,
    ) -> AsyncGenerator[dict, None]:
        """
        Full ReAct loop.

        Each iteration:
          1. Reason  — LLM generates a response (may include TOOL_CALLs)
          2. Act     — execute any tool calls found
          3. Observe — inject results back as observations
          4. Repeat  — until TASK_COMPLETE or no tool calls or max iterations
        """
        sp = system_prompt or AGENT_SYSTEM_PROMPT
        self.conversation.append({"role": "user", "content": user_message})
        _memory.save_turn(self.user_id, "user", user_message)

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

            async for token in self.llm.chat_stream(self.conversation, system=sp):
                full_response += token
                yield {"type": "token", "data": token}
                for call in self._extract_tool_calls(full_response):
                    if call not in tool_calls_found:
                        tool_calls_found.append(call)

            self.conversation.append({"role": "assistant", "content": full_response})
            _memory.save_turn(self.user_id, "assistant", full_response)

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
                    break

                result_dict = result.to_dict()
                yield {"type": "tool_result", "data": result_dict}

                if result_dict.get("success"):
                    obs = (
                        f"[OBSERVATION — {skill_name}.{action} ✓]\n"
                        f"{json.dumps(result_dict.get('output'), indent=2)}"
                    )
                else:
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
                        "phase": "tool failure — self-healing on next iter",
                        "healing": True,
                    }}

                observations.append(obs)

            if hit_confirm:
                yield {"type": "done", "data": {"react_paused": True, "iterations": iteration}}
                return

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
        self, call: dict, auto_confirm: bool, system_prompt: str = None
    ) -> AsyncGenerator[dict, None]:
        """Execute one tool call, yielding tool_call / confirm_needed / tool_result."""
        sp = system_prompt or AGENT_SYSTEM_PROMPT
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
        lower = text.lower()
        if "task_complete" in text or "TASK_COMPLETE" in text:
            return True
        return any(signal in lower for signal in COMPLETION_SIGNALS)

    def _extract_tool_calls(self, text: str) -> list:
        """
        Parse TOOL_CALL: {...} blocks from LLM output.
        Brace-counting instead of regex — handles nested JSON correctly.
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
