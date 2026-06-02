"""
Memory Manager — per-user conversation history persisted as Markdown files.

Design principles:
  • One .md file per user_id under MEMORY_DIR.
  • History is NEVER sent to the LLM automatically — it is injected only when
    the user explicitly requests retrieval (e.g. "show last 10 conversations").
  • Each saved entry records: timestamp, role, and message content.
  • Large file attachments are summarised (not stored verbatim) to keep files lean.

Public API:
  manager = MemoryManager()
  manager.save_turn(user_id, role, content)
  history = manager.retrieve_last_n(user_id, n=10)  → formatted Markdown string
  manager.clear(user_id)                             → wipes the user's file
"""

import os
import re
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("memory_manager")

# ── Configuration ──────────────────────────────────────────────────────────────
MEMORY_DIR = Path(os.getenv("MEMORY_DIR", "/app/data/memory"))
MAX_CONTENT_CHARS = 2000   # truncate individual messages longer than this
ATTACHMENT_PLACEHOLDER = "[attachment: {name} ({mime}, {size})]"

# ── Detect if user is asking for history retrieval ─────────────────────────────
_RETRIEVAL_PATTERNS = [
    r"retrieve\s+(?:last\s+)?(\d+)\s+conversations?",
    r"show\s+(?:last\s+)?(\d+)\s+conversations?",
    r"last\s+(\d+)\s+conversations?",
    r"conversation\s+history",
    r"show\s+(?:my\s+)?history",
    r"recall\s+(?:last\s+)?(\d+)",
    r"remember\s+(?:last\s+)?(\d+)",
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in _RETRIEVAL_PATTERNS]


def detect_retrieval_request(text: str) -> Optional[int]:
    """
    Returns the number of conversations requested if the message is a
    history-retrieval request, otherwise returns None.

    Examples:
      "retrieve last 10 conversations" → 10
      "show my conversation history"   → 20 (default)
      "what is the weather"            → None
    """
    for pattern in _COMPILED:
        m = pattern.search(text)
        if m:
            try:
                return int(m.group(1))
            except (IndexError, TypeError):
                return 20   # default count when no number given
    return None


# ── MemoryManager ──────────────────────────────────────────────────────────────

class MemoryManager:
    def __init__(self, memory_dir: Path = MEMORY_DIR):
        self.memory_dir = memory_dir
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, user_id: str) -> Path:
        # Sanitise user_id to a safe filename
        safe = re.sub(r"[^\w\-]", "_", user_id)[:64] or "default"
        return self.memory_dir / f"{safe}.md"

    # ── Write ──────────────────────────────────────────────────────────────────

    def save_turn(
        self,
        user_id: str,
        role: str,
        content: str,
        attachments: Optional[list] = None,
    ) -> None:
        """
        Append a single conversation turn to the user's history file.

        Args:
            user_id:     Identifier for the user (e.g. "default", session ID).
            role:        "user" | "assistant" | "tool_call" | "tool_result"
            content:     Message text.
            attachments: Optional list of dicts with keys: name, mime, size.
        """
        path = self._path(user_id)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        # Summarise oversized content
        body = content
        if len(body) > MAX_CONTENT_CHARS:
            body = body[:MAX_CONTENT_CHARS] + f"\n… [truncated, {len(content)} chars total]"

        # Append attachment placeholders
        att_lines = ""
        if attachments:
            for att in attachments:
                att_lines += "\n" + ATTACHMENT_PLACEHOLDER.format(
                    name=att.get("name", "file"),
                    mime=att.get("mime", "unknown"),
                    size=att.get("size", "?"),
                )

        role_display = role.upper()
        entry = (
            f"\n---\n"
            f"**[{ts}] {role_display}**\n\n"
            f"{body}{att_lines}\n"
        )

        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(entry)
        except OSError as e:
            logger.error("MemoryManager: failed to write %s: %s", path, e)

    def save_conversation_block(
        self,
        user_id: str,
        user_msg: str,
        assistant_msg: str,
        attachments: Optional[list] = None,
    ) -> None:
        """Convenience: save a complete user+assistant exchange in one call."""
        self.save_turn(user_id, "user", user_msg, attachments=attachments)
        self.save_turn(user_id, "assistant", assistant_msg)

    # ── Read ───────────────────────────────────────────────────────────────────

    def retrieve_last_n(self, user_id: str, n: int = 10) -> str:
        """
        Return the last *n* conversation turns as a formatted Markdown string,
        suitable for injection into the LLM context.

        Returns an empty string if there is no history.
        """
        path = self._path(user_id)
        if not path.exists():
            return "_No conversation history found for this user._"

        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as e:
            logger.error("MemoryManager: failed to read %s: %s", path, e)
            return "_Error reading conversation history._"

        # Split on the separator we write between entries
        parts = [p.strip() for p in raw.split("\n---\n") if p.strip()]
        last_n = parts[-n:] if len(parts) > n else parts

        if not last_n:
            return "_No conversation history found for this user._"

        header = f"### Last {len(last_n)} conversation turn(s) for user `{user_id}`:\n\n"
        return header + "\n\n---\n\n".join(last_n)

    def retrieve_all(self, user_id: str) -> str:
        """Return entire history as a raw Markdown string."""
        path = self._path(user_id)
        if not path.exists():
            return "_No conversation history._"
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return "_Error reading history._"

    # ── Manage ─────────────────────────────────────────────────────────────────

    def clear(self, user_id: str) -> bool:
        """Delete the user's history file. Returns True if it existed."""
        path = self._path(user_id)
        if path.exists():
            path.unlink()
            logger.info("MemoryManager: cleared history for %s", user_id)
            return True
        return False

    def list_users(self) -> list:
        """Return a list of user IDs that have history files."""
        return [p.stem for p in self.memory_dir.glob("*.md")]

    def file_size(self, user_id: str) -> int:
        """Return the size of the user's history file in bytes, or 0."""
        path = self._path(user_id)
        return path.stat().st_size if path.exists() else 0
