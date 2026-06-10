"""
core/prompt_builder.py — re-exports from top-level prompt_builder.py
so that `from core.prompt_builder import ...` works correctly.

version: 1.0.0
changelog:
  1.0.0 - 2026-06-10 - Shim module; all logic lives in top-level prompt_builder.py
"""
from prompt_builder import build_system_prompt, AGENT_SYSTEM_PROMPT

__all__ = ["build_system_prompt", "AGENT_SYSTEM_PROMPT"]
