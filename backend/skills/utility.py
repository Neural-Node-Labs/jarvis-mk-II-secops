"""
Skills: summarizer, memory_writer
Category: UTILITY
"""
from __future__ import annotations

import json
import os
import urllib.request

from core.skill_registry import SkillRegistry, SkillResult
from skills._base import Skill

DEEPSEEK_URL   = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

def _call_llm(system: str, messages: list[dict]) -> str:
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY not set")
    payload = json.dumps({
        "model": DEEPSEEK_MODEL, "max_tokens": 512, "temperature": 0.3,
        "messages": [{"role": "system", "content": system}] + messages,
    }).encode()
    req = urllib.request.Request(DEEPSEEK_URL, data=payload, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"]


class SummarizerSkill(Skill):
    name             = "summarizer"
    description      = "Summarize text using DeepSeek LLM"
    usage            = "summarizer <text>"
    trigger_patterns = ["summarize", "tldr", "condense"]

    def run(self, args: str, mm=None) -> SkillResult:
        if not args.strip():
            return SkillResult(False, f"Usage: {self.usage}")
        try:
            s = _call_llm(
                "Return only a concise summary, no preamble.",
                [{"role": "user", "content": f"Summarize:\n{args[:6000]}"}],
            )
            return SkillResult(True, f"**Summary:**\n{s}")
        except Exception as e:
            return SkillResult(False, f"⚠ {e}")


class MemoryWriterSkill(Skill):
    name             = "memory_writer"
    description      = "Write a fact to long-term memory (requires MemoryManager)"
    usage            = "memory_writer <text>"
    trigger_patterns = ["remember this", "save to memory", "note this"]

    def run(self, args: str, mm=None) -> SkillResult:
        if not args.strip():
            return SkillResult(False, f"Usage: {self.usage}")
        if mm is None:
            # Gracefully degrade if no memory manager is provided
            return SkillResult(True, f"✓ (no memory manager attached) Note: {args[:200]}")
        try:
            e = mm.archive.store(args.strip(), source="skill_memory_writer", tags=["fact"])
            return SkillResult(True, f"✓ Stored → `{e.id}`")
        except Exception as exc:
            return SkillResult(False, f"⚠ Memory write failed: {exc}")


def register(registry):
    registry.register("summarizer",    SummarizerSkill())
    registry.register("memory_writer", MemoryWriterSkill())
