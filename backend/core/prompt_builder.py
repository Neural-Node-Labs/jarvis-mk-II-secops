"""
core/prompt_builder.py — shim re-exporting from top-level prompt_builder.py
version: 2.0.0
"""
from prompt_builder import (
    build_system_prompt,
    AGENT_SYSTEM_PROMPT,
    list_personas,
    DEFAULT_PERSONA,
    PERSONAS_DIR,
    get_persona_soul,
    get_persona,
    save_persona,
    delete_persona,
    load_all_personas,
)
__all__ = [
    "build_system_prompt", "AGENT_SYSTEM_PROMPT", "list_personas",
    "DEFAULT_PERSONA", "PERSONAS_DIR", "get_persona_soul",
    "get_persona", "save_persona", "delete_persona", "load_all_personas",
]
