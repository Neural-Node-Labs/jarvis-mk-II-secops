"""
Agent Core — Orchestrates LLM + Skills.
Handles tool-call parsing, confirmation flow, streaming responses,
and native ReAct (Reason→Act→Observe) loop with self-healing.
No Pydantic anywhere.

version: 4.0.0
changelog:
  1.0.0 - Initial agent with single Reason→Act cycle
  2.0.0 - Native ReAct loop with self-healing and react_status events
  3.0.0 - System prompt extracted to prompt_builder.py. MemoryManager integration.
          File attachment support.
  4.0.0 - No Pydantic. CBD-aligned structure. Conversation pruning to prevent
          context overflow. Dedup tool calls within a response. Memory save_turn
          on every react iteration. Hardened brace-counting JSON extractor.
"""
import json
import logging
from typing import AsyncGenerator, Optional

from core.llm_router import LLMRouter, LLMConfig
from core.skill_registry import SkillRegistry
from core.prompt_builder import build_system_prompt, AGENT_SYSTEM_PROMPT
from core.memory_manager import MemoryManager, detect_retrieval_request

logger = logging.getLogger("agent.core")

# ── Constants ─────────────────────────────────────────────────────────────────
REACT_MAX_ITERATIONS   = 30
MAX_CONVERSATION_TURNS = 80      # prune oldest turns beyond this to stay in context
TOOL_CALL_MARKER       = "TOOL_CALL:"

COMPLETION_SIGNALS = {
    "task_complete", "task complete", "task is complete", "task completed",
    "all done", "i have completed", "successfully completed",
    "the task is done", "work is complete",
}

# ── Module-level memory (shared across all agents in this process) ─────────────
_memory = MemoryManager()


# ── Attachment formatter ───────────────────────────────────────────────────────

def _format_attachment(att: dict) -> str:
    """Render an uploaded file dict as an inline block for the LLM."""
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


# ── Agent ──────────────────────────────────────────────────────────────────────

class Agent:
    """
    Jarvis MKII agent core.

    Each instance has:
      - Its own LLMRouter (provider + model)
      - Its own SkillRegistry (skill dispatch)
      - Its own conversation buffer (list of message dicts)
      - Its own pending_confirms registry
      - Its own user_id (for memory persistence)
    """

    def __init__(self, config: LLMConfig, registry: SkillRegistry, user_id: str = "default"):
        self.llm      = LLMRouter(config)
        self.registry = registry
        self.user_id  = user_id

        self.conversation:    list = []
        self.pending_confirms: dict = {}   # confirm_id → (skill, action, params)
        self._confirm_counter: int  = 0

    def reset(self):
        """Clear conversation state. Memory file on disk is preserved."""
        self.conversation     = []
        self.pending_confirms = {}
        self._confirm_counter = 0

    # ── Conversation management ────────────────────────────────────────────────

    def _append(self, role: str, content: str):
        """Append a turn and prune if the conversation exceeds MAX_CONVERSATION_TURNS."""
        self.conversation.append({"role": role, "content": content})
        if len(self.conversation) > MAX_CONVERSATION_TURNS:
            # Keep first turn (original task) + most recent turns
            self.conversation = self.conversation[:1] + self.conversation[-(MAX_CONVERSATION_TURNS - 1):]

    # ── Public entry points ────────────────────────────────────────────────────

    async def chat_stream(
        self,
        user_message: str,
        react:        bool = False,
        auto_confirm: bool = False,
        attachments:  Optional[list] = None,
    ) -> AsyncGenerator[dict, None]:
        """
        Main entry point.
        react=False → single Reason→Act cycle.
        react=True  → full ReAct loop (up to REACT_MAX_ITERATIONS).
        """
        # Inject file attachments into the message
        if attachments:
            blocks = [_format_attachment(a) for a in attachments]
            user_message = "\n\n".join(blocks) + "\n\n" + user_message

        # Check for memory retrieval request — inject history into system prompt
        n = detect_retrieval_request(user_message)
        system_prompt = AGENT_SYSTEM_PROMPT
        if n is not None:
            history = _memory.retrieve_last_n(self.user_id, n)
            system_prompt = build_system_prompt(memory_context=history)
            logger.info("[memory_injected] user=%s turns=%d", self.user_id, n)

        if react:
            async for event in self._react_loop(user_message, auto_confirm, system_prompt):
                yield event
        else:
            async for event in self._single_cycle(user_message, auto_confirm, system_prompt):
                yield event

    async def confirm_action(self, confirm_id: str) -> AsyncGenerator[dict, None]:
        """Resume a paused agent by confirming a previously-gated destructive action."""
        if confirm_id not in self.pending_confirms:
            yield {"type": "error", "data": "Confirmation ID not found or already used."}
            return

        skill_name, action, params = self.pending_confirms.pop(confirm_id)
        yield {"type": "tool_call", "data": {"skill": skill_name, "action": action, "params": params, "confirmed": True}}

        result      = await self.registry.execute(skill_name, action, params, confirmed=True)
        result_dict = result.to_dict()
        # Embed skill/action/params so frontend telemetry can correlate without cross-event state
        yield {"type": "tool_result", "data": {"skill": skill_name, "action": action, "params": params, **result_dict}}

        ctx = f"[CONFIRMED TOOL RESULT: {skill_name}.{action}]\n{json.dumps(result_dict, indent=2)}"
        self._append("user", ctx)

        summary = ""
        async for token in self.llm.chat_stream(self.conversation, system=AGENT_SYSTEM_PROMPT):
            summary += token
            yield {"type": "token", "data": token}

        self._append("assistant", summary or "✓")
        _memory.save_turn(self.user_id, "assistant", summary or "✓")
        yield {"type": "done", "data": {}}

    # ── Single cycle ───────────────────────────────────────────────────────────

    async def _single_cycle(
        self,
        user_message: str,
        auto_confirm: bool = False,
        system_prompt: str = None,
    ) -> AsyncGenerator[dict, None]:
        """One Reason→Act cycle: LLM → tool calls → summary."""
        sp = system_prompt or AGENT_SYSTEM_PROMPT
        self._append("user", user_message)

        full_response = ""
        tool_calls_found: list = []
        yield {"type": "token", "data": ""}

        async for token in self.llm.chat_stream(self.conversation, system=sp):
            full_response += token
            yield {"type": "token", "data": token}
            for call in self._extract_tool_calls(full_response):
                if call not in tool_calls_found:
                    tool_calls_found.append(call)

        self._append("assistant", full_response)
        _memory.save_conversation_block(self.user_id, user_message, full_response)

        for call in tool_calls_found:
            async for event in self._execute_call(call, auto_confirm, sp):
                yield event
                if event.get("type") == "confirm_needed":
                    yield {"type": "done", "data": {}}
                    return

            # Post-tool LLM summary
            summary = ""
            async for token in self.llm.chat_stream(self.conversation, system=sp):
                summary += token
                yield {"type": "token", "data": token}
            self._append("assistant", summary or "✓")
            _memory.save_turn(self.user_id, "assistant", summary or "✓")

        yield {"type": "done", "data": {}}

    # ── ReAct loop ─────────────────────────────────────────────────────────────

    async def _react_loop(
        self,
        user_message: str,
        auto_confirm: bool = False,
        system_prompt: str = None,
    ) -> AsyncGenerator[dict, None]:
        """
        Full ReAct loop: Reason→Act→Observe repeated until:
          - TASK_COMPLETE signal detected
          - No tool calls in response (agent finished or needs input)
          - Confirmation required (loop pauses)
          - REACT_MAX_ITERATIONS reached
        """
        sp = system_prompt or AGENT_SYSTEM_PROMPT
        self._append("user", user_message)
        _memory.save_turn(self.user_id, "user", user_message)

        for iteration in range(1, REACT_MAX_ITERATIONS + 1):
            yield {"type": "react_status", "data": {
                "iteration": iteration,
                "max":       REACT_MAX_ITERATIONS,
                "phase":     "reasoning",
                "healing":   False,
            }}

            # ── REASON ────────────────────────────────────────────────────────
            full_response    = ""
            tool_calls_found: list = []
            yield {"type": "token", "data": ""}

            async for token in self.llm.chat_stream(self.conversation, system=sp):
                full_response += token
                yield {"type": "token", "data": token}
                for call in self._extract_tool_calls(full_response):
                    if call not in tool_calls_found:
                        tool_calls_found.append(call)

            self._append("assistant", full_response)
            _memory.save_turn(self.user_id, "assistant", full_response)

            # ── Completion check ───────────────────────────────────────────────
            if self._is_complete(full_response):
                yield {"type": "react_status", "data": {
                    "iteration": iteration, "max": REACT_MAX_ITERATIONS,
                    "phase": "complete", "healing": False,
                }}
                yield {"type": "done", "data": {"react_complete": True, "iterations": iteration}}
                return

            # ── No tool calls → awaiting input or task done ────────────────────
            if not tool_calls_found:
                yield {"type": "react_status", "data": {
                    "iteration": iteration, "max": REACT_MAX_ITERATIONS,
                    "phase": "awaiting_input", "healing": False,
                }}
                yield {"type": "done", "data": {"react_complete": False, "iterations": iteration}}
                return

            # ── ACT ───────────────────────────────────────────────────────────
            yield {"type": "react_status", "data": {
                "iteration": iteration, "max": REACT_MAX_ITERATIONS,
                "phase":     f"acting — {len(tool_calls_found)} tool call(s)",
                "healing":   False,
            }}

            observations: list = []
            hit_confirm = False

            for call in tool_calls_found:
                skill_name = call.get("skill", "")
                action     = call.get("action", "")
                params     = call.get("params", {})

                yield {"type": "tool_call", "data": {"skill": skill_name, "action": action, "params": params}}

                result = await self.registry.execute(skill_name, action, params, confirmed=auto_confirm)

                # Confirmation gate
                if result.requires_confirm and not auto_confirm:
                    self._confirm_counter += 1
                    confirm_id = f"{skill_name}:{action}:{self._confirm_counter}"
                    self.pending_confirms[confirm_id] = (skill_name, action, params)
                    yield {"type": "confirm_needed", "data": {
                        "confirm_id": confirm_id,
                        "prompt":     result.confirm_prompt,
                        "skill":      skill_name,
                        "action":     action,
                    }}
                    hit_confirm = True
                    break

                result_dict = result.to_dict()
                yield {"type": "tool_result", "data": {"skill": skill_name, "action": action, "params": params, **result_dict}}

                # ── OBSERVE ────────────────────────────────────────────────────
                if result_dict.get("success"):
                    obs = (
                        f"[OBSERVATION — {skill_name}.{action} ✓]\n"
                        f"{json.dumps(result_dict.get('output'), indent=2, default=str)}"
                    )
                else:
                    obs = (
                        f"[OBSERVATION — {skill_name}.{action} ✗ FAILED]\n"
                        f"Error: {result_dict.get('error')}\n"
                        f"Self-healing: Diagnose why {action} on {skill_name} failed. "
                        f"Adjust parameters or strategy and retry in the next iteration."
                    )
                    yield {"type": "react_status", "data": {
                        "iteration": iteration, "max": REACT_MAX_ITERATIONS,
                        "phase": "tool_failure — self-healing", "healing": True,
                    }}

                observations.append(obs)

            if hit_confirm:
                yield {"type": "done", "data": {"react_paused": True, "iterations": iteration}}
                return

            if observations:
                self._append("user", "\n\n".join(observations))

        # ── Max iterations ─────────────────────────────────────────────────────
        yield {"type": "react_status", "data": {
            "iteration": REACT_MAX_ITERATIONS, "max": REACT_MAX_ITERATIONS,
            "phase": "max_iterations_reached", "healing": False,
        }}
        yield {"type": "done", "data": {
            "react_complete": False,
            "iterations":     REACT_MAX_ITERATIONS,
            "reason":         "max_iterations",
        }}

    # ── Helpers ────────────────────────────────────────────────────────────────

    async def _execute_call(
        self,
        call:         dict,
        auto_confirm: bool,
        system_prompt: str = None,
    ) -> AsyncGenerator[dict, None]:
        """Execute one tool call. Yields events. Appends result to conversation."""
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
                "confirm_id": confirm_id,
                "prompt":     result.confirm_prompt,
                "skill":      skill_name,
                "action":     action,
            }}
        else:
            result_dict = result.to_dict()
            yield {"type": "tool_result", "data": {"skill": skill_name, "action": action, "params": params, **result_dict}}
            ctx = f"[TOOL RESULT: {skill_name}.{action}]\n{json.dumps(result_dict, indent=2, default=str)}"
            self._append("user", ctx)

    def _is_complete(self, text: str) -> bool:
        if "TASK_COMPLETE" in text or "task_complete" in text:
            return True
        lower = text.lower().strip()
        return any(signal in lower for signal in COMPLETION_SIGNALS)

    def _extract_tool_calls(self, text: str) -> list:
        """
        Parse all TOOL_CALL: {...} blocks from LLM output.
        Uses brace-counting to handle nested JSON correctly.
        Deduplicates calls within the same text window.
        """
        calls:  list = []
        seen:   set  = set()
        start = 0

        while True:
            idx = text.find(TOOL_CALL_MARKER, start)
            if idx == -1:
                break

            brace_start = text.find("{", idx + len(TOOL_CALL_MARKER))
            if brace_start == -1:
                break

            depth = 0
            i     = brace_start
            while i < len(text):
                ch = text[i]
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        json_str = text[brace_start: i + 1]
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
