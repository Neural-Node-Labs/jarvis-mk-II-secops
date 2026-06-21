"""
Agent Core — Compatibility facade over the Blackbox Brain.

version: 5.1.0
changelog:
  1.0.0 - Initial agent with single Reason→Act cycle
  2.0.0 - Native ReAct loop with self-healing and react_status events
  3.0.0 - System prompt extracted to prompt_builder.py. MemoryManager integration.
          File attachment support.
  4.0.0 - No Pydantic. CBD-aligned structure. Conversation pruning to prevent
          context overflow. Dedup tool calls within a response. Memory save_turn
          on every react iteration. Hardened brace-counting JSON extractor.
  5.0.0 - REPLACED the single-track ReAct engine with the Blackbox Brain
          (core/blackbox_brain.py): Classifier → Chat/Task split → Planner →
          asyncio Swarm → Consolidation. That module is now the actual
          orchestration logic / "central nervous system" for the whole app.
          This file is kept ONLY so `from core.agent import Agent` keeps
          working everywhere it already did (main.py's session registry,
          the JarvisMKII multi-task skill, etc.) — Agent IS BlackboxBrain,
          same constructor, same chat_stream()/confirm_action() event
          protocol (token / react_status / tool_call / tool_result /
          confirm_needed / done / error). No other file needed to change
          for this swap.
  5.1.0 - Added DEEP_TASK_MAX_ITERATIONS export. IMPORTANT VALUE-DRIFT NOTE:
          REACT_MAX_ITERATIONS below is kept ONLY as a name-compat alias for
          DEFAULT_RE_ACT_MAX_LOOP (the swarm's default per-worker cap = 3).
          Pre-5.0.0 code imported this name expecting the old single-track
          agent's iteration budget (30). Any call site that wants THAT
          budget (e.g. /api/evolve's force_single_task=True path) must use
          DEEP_TASK_MAX_ITERATIONS instead — using REACT_MAX_ITERATIONS
          there silently caps it at 3, defeating the whole point of
          force_single_task. See core/blackbox_brain.py's constants section.
"""
from core.blackbox_brain import (
    BlackboxBrain as Agent,
    Task,
    DEFAULT_RE_ACT_MAX_LOOP as REACT_MAX_ITERATIONS,
    DEEP_TASK_MAX_ITERATIONS,
    MAX_CONVERSATION_TURNS,
    TOOL_CALL_MARKER,
)
from core.prompt_builder import build_system_prompt, AGENT_SYSTEM_PROMPT, DEFAULT_PERSONA

__all__ = [
    "Agent", "Task", "REACT_MAX_ITERATIONS", "DEEP_TASK_MAX_ITERATIONS", "MAX_CONVERSATION_TURNS",
    "TOOL_CALL_MARKER", "build_system_prompt", "AGENT_SYSTEM_PROMPT", "DEFAULT_PERSONA",
]
