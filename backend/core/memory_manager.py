"""
Memory Manager — Per-user conversation history persistence.
Implements EpisodicStore from memory-blueprint.md.
Stores to Markdown files on disk. No external DB required.

version: 2.0.0
changelog:
  1.0.0 - Initial file-backed memory with save/retrieve
  2.0.0 - No Pydantic. Added detect_retrieval_request(), clear(), search().
          Thread-safe file writes. Memory dir configurable via env.
          Graceful failure — memory errors never block the agent.
"""
import os
import re
import logging
import threading
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("memory_manager")

MEMORY_DIR   = os.getenv("MEMORY_DIR", "./data/memory")
MAX_TURNS    = int(os.getenv("MEMORY_MAX_TURNS", "200"))   # Max turns kept per user

# ── Retrieval trigger patterns ─────────────────────────────────────────────────
_RETRIEVAL_PATTERNS = [
    r"\b(continue|resume|pick\s*up)\b",
    r"retrieve\s+(?:last\s+)?(\d+)\s+conversations?",
    r"show\s+(?:me\s+)?(?:the\s+)?(?:last\s+)?(\d+)\s+conversations?",
    r"conversation\s+history",
    r"what\s+did\s+(?:we|i)\s+(?:talk|discuss|do)\s+(?:last|before|earlier)",
]

_RETRIEVAL_N_PATTERN = re.compile(r"(\d+)\s+conversations?", re.IGNORECASE)

_WRITE_LOCK = threading.Lock()


def detect_retrieval_request(message: str) -> Optional[int]:
    """
    Scan a user message for memory retrieval triggers.
    Returns the number of turns requested, or None if not a retrieval request.
    Default n=5 if a trigger matches but no number specified.
    """
    lower = message.lower()
    for pattern in _RETRIEVAL_PATTERNS:
        if re.search(pattern, lower):
            m = _RETRIEVAL_N_PATTERN.search(message)
            n = int(m.group(1)) if m else 5
            return min(n, 20)  # hard cap: 20 turns max injection
    return None


class MemoryManager:
    """
    File-backed per-user conversation memory.
    Each user gets a Markdown file at MEMORY_DIR/{user_id}.md

    All public methods are safe — exceptions are caught and logged,
    never propagated to the caller (memory must never block the agent).
    """

    def __init__(self, memory_dir: str = None):
        self.memory_dir = memory_dir or MEMORY_DIR
        self._ensure_dir()

    def _ensure_dir(self):
        try:
            os.makedirs(self.memory_dir, exist_ok=True)
        except Exception as exc:
            logger.error("[memory_dir_error] %s", exc)

    def _user_path(self, user_id: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", user_id)[:64]
        return os.path.join(self.memory_dir, f"{safe}.md")

    # ── Write API ──────────────────────────────────────────────────────────────

    def save_turn(self, user_id: str, role: str, content: str) -> None:
        """Append a single turn to the user's memory file."""
        try:
            ts      = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            content = content[:4000]  # cap per turn to avoid bloat
            block   = f"\n### [{ts}] {role.upper()}\n{content}\n"
            path    = self._user_path(user_id)
            with _WRITE_LOCK:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(block)
        except Exception as exc:
            logger.warning("[memory_save_turn_error] user=%s err=%s", user_id, exc)

    def save_conversation_block(self, user_id: str, user_msg: str, assistant_msg: str) -> None:
        """Append a user+assistant exchange as a block."""
        self.save_turn(user_id, "user", user_msg)
        self.save_turn(user_id, "assistant", assistant_msg)

    # ── Read API ───────────────────────────────────────────────────────────────

    def retrieve_last_n(self, user_id: str, n: int = 5) -> str:
        """
        Return the last n conversation turns as a formatted string.
        Returns empty string if no history exists.
        """
        try:
            path = self._user_path(user_id)
            if not os.path.isfile(path):
                return ""
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            # Split by turn headers and take last n*2 (user+assistant pairs)
            blocks = re.split(r"(?=### \[)", content)
            blocks = [b.strip() for b in blocks if b.strip()]
            recent = blocks[-(n * 2):]
            return "\n\n".join(recent)
        except Exception as exc:
            logger.warning("[memory_retrieve_error] user=%s err=%s", user_id, exc)
            return ""

    def search(self, user_id: str, query: str, max_results: int = 5) -> list[str]:
        """
        Full-text search across a user's memory file.
        Returns matching turn blocks (case-insensitive).
        """
        try:
            path = self._user_path(user_id)
            if not os.path.isfile(path):
                return []
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            blocks  = re.split(r"(?=### \[)", content)
            query_l = query.lower()
            matches = [b.strip() for b in blocks if query_l in b.lower()]
            return matches[-max_results:]
        except Exception as exc:
            logger.warning("[memory_search_error] user=%s err=%s", user_id, exc)
            return []

    def clear(self, user_id: str) -> bool:
        """Delete all stored memory for a user. Returns True on success."""
        try:
            path = self._user_path(user_id)
            if os.path.isfile(path):
                with _WRITE_LOCK:
                    os.remove(path)
            logger.info("[memory_cleared] user=%s", user_id)
            return True
        except Exception as exc:
            logger.warning("[memory_clear_error] user=%s err=%s", user_id, exc)
            return False

    def get_stats(self, user_id: str) -> dict:
        """Return basic stats about a user's memory file."""
        try:
            path = self._user_path(user_id)
            if not os.path.isfile(path):
                return {"exists": False, "turns": 0, "size_bytes": 0}
            size = os.path.getsize(path)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            turns = len(re.findall(r"^### \[", content, re.MULTILINE))
            return {"exists": True, "turns": turns, "size_bytes": size, "path": path}
        except Exception as exc:
            logger.warning("[memory_stats_error] user=%s err=%s", user_id, exc)
            return {"exists": False, "error": str(exc)}
