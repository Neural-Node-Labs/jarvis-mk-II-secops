"""
FileStreamerSkill - Handles large file writing using segmented chunk appending.

Resolves LLM max_token truncation by splitting file writes across multiple
agent turns: start_file → append_chunk (×N) → finalize_file.

CBD Contract:
  IN:  action ∈ {start_file, append_chunk, finalize_file}, params dict
  OUT: SkillResult(success, output, error)

Process-safety: stateless — uses only disk-level checks (os.path.exists),
no in-memory sets or instance-level tracking. Safe across OS worker processes.
"""

import os
import logging
from core.skill_registry import SkillRegistry, SkillResult

logger = logging.getLogger("skill.file_streamer")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SENTINEL_SUFFIX = ".fstream_open"   # Marker file that signals an active stream


def _sentinel_path(filepath: str) -> str:
    """Return the path of the sentinel marker for *filepath*."""
    return filepath + _SENTINEL_SUFFIX


def _ensure_parent_dir(filepath: str) -> None:
    """Create parent directories if they do not exist."""
    parent = os.path.dirname(filepath)
    if parent:
        os.makedirs(parent, exist_ok=True)


# ---------------------------------------------------------------------------
# Skill class
# ---------------------------------------------------------------------------
class FileStreamerSkill:
    """
    Stateless skill that enables writing arbitrarily large files across
    multiple LLM turns by chunking content into sequential appends.

    Actions
    -------
    start_file      Create (or overwrite) the target file and open the stream.
    append_chunk    Append a content chunk to an already-started stream.
    finalize_file   Close the stream (remove sentinel) and confirm the write.
    """

    description = (
        "Handles large file payloads by allowing chunked, segmented appending "
        "to bypass LLM max_token generation constraints. Essential for writing "
        "files that exceed single-turn output limits."
    )
    actions = ["start_file", "append_chunk", "finalize_file"]

    # ------------------------------------------------------------------
    # Public dispatch entry-point (called by SkillRegistry.execute)
    # ------------------------------------------------------------------
    async def execute(self, action: str, params: dict, confirmed: bool = False) -> SkillResult:
        dispatch = {
            "start_file":    self._start_file,
            "append_chunk":  self._append_chunk,
            "finalize_file": self._finalize_file,
        }
        handler = dispatch.get(action)
        if handler is None:
            return SkillResult(
                False, None,
                error=f"Unknown action '{action}'. Valid actions: {self.actions}"
            )
        return await handler(params)

    # ------------------------------------------------------------------
    # Action: start_file
    # ------------------------------------------------------------------
    async def _start_file(self, params: dict) -> SkillResult:
        """
        IN params:
          filepath  (str, required)  — destination path, relative or absolute
          overwrite (bool, optional) — default False; if False and file exists,
                                       returns an error rather than clobbering
        OUT output:
          {"filepath": str, "status": "stream_opened", "bytes_written": 0}
        """
        filepath = params.get("filepath")
        if not filepath:
            return SkillResult(False, None, error="'filepath' is required for start_file.")

        overwrite: bool = bool(params.get("overwrite", False))

        # Guard: refuse to overwrite unless explicitly allowed
        if os.path.exists(filepath) and not overwrite:
            return SkillResult(
                False, None,
                error=(
                    f"File '{filepath}' already exists. "
                    "Set overwrite=true to replace it."
                )
            )

        # Guard: another stream already open for this path
        sentinel = _sentinel_path(filepath)
        if os.path.exists(sentinel):
            return SkillResult(
                False, None,
                error=(
                    f"A stream is already open for '{filepath}'. "
                    "Call finalize_file to close it before starting a new one."
                )
            )

        try:
            _ensure_parent_dir(filepath)
            # Truncate / create the target file
            with open(filepath, "w", encoding="utf-8") as fh:
                fh.write("")  # empty — content comes via append_chunk

            # Create sentinel marker
            with open(sentinel, "w", encoding="utf-8") as fh:
                fh.write(filepath)

            logger.info("Stream opened: %s", filepath)
            return SkillResult(
                True,
                output={
                    "filepath": filepath,
                    "status": "stream_opened",
                    "bytes_written": 0,
                }
            )
        except OSError as exc:
            return SkillResult(False, None, error=f"start_file OSError: {exc}")

    # ------------------------------------------------------------------
    # Action: append_chunk
    # ------------------------------------------------------------------
    async def _append_chunk(self, params: dict) -> SkillResult:
        """
        IN params:
          filepath (str, required) — must match a previously started stream
          content  (str, required) — text chunk to append (no size limit)
        OUT output:
          {"filepath": str, "status": "chunk_appended", "chunk_bytes": int,
           "total_bytes": int}
        """
        filepath = params.get("filepath")
        if not filepath:
            return SkillResult(False, None, error="'filepath' is required for append_chunk.")

        content = params.get("content")
        if content is None:
            return SkillResult(False, None, error="'content' is required for append_chunk.")

        sentinel = _sentinel_path(filepath)
        if not os.path.exists(sentinel):
            return SkillResult(
                False, None,
                error=(
                    f"No open stream found for '{filepath}'. "
                    "Call start_file first."
                )
            )

        if not os.path.exists(filepath):
            return SkillResult(
                False, None,
                error=f"Target file '{filepath}' is missing — stream may be corrupt."
            )

        try:
            chunk_bytes = len(content.encode("utf-8"))
            with open(filepath, "a", encoding="utf-8") as fh:
                fh.write(content)

            total_bytes = os.path.getsize(filepath)
            logger.info("Chunk appended to %s (%d bytes, total %d)", filepath, chunk_bytes, total_bytes)
            return SkillResult(
                True,
                output={
                    "filepath": filepath,
                    "status": "chunk_appended",
                    "chunk_bytes": chunk_bytes,
                    "total_bytes": total_bytes,
                }
            )
        except OSError as exc:
            return SkillResult(False, None, error=f"append_chunk OSError: {exc}")

    # ------------------------------------------------------------------
    # Action: finalize_file
    # ------------------------------------------------------------------
    async def _finalize_file(self, params: dict) -> SkillResult:
        """
        IN params:
          filepath (str, required) — path of the stream to close
        OUT output:
          {"filepath": str, "status": "stream_finalized", "total_bytes": int}
        """
        filepath = params.get("filepath")
        if not filepath:
            return SkillResult(False, None, error="'filepath' is required for finalize_file.")

        sentinel = _sentinel_path(filepath)
        if not os.path.exists(sentinel):
            return SkillResult(
                False, None,
                error=(
                    f"No open stream found for '{filepath}'. "
                    "Either it was already finalized or start_file was never called."
                )
            )

        try:
            os.remove(sentinel)
            total_bytes = os.path.getsize(filepath) if os.path.exists(filepath) else 0
            logger.info("Stream finalized: %s (%d bytes)", filepath, total_bytes)
            return SkillResult(
                True,
                output={
                    "filepath": filepath,
                    "status": "stream_finalized",
                    "total_bytes": total_bytes,
                }
            )
        except OSError as exc:
            return SkillResult(False, None, error=f"finalize_file OSError: {exc}")


# ---------------------------------------------------------------------------
# Registration hook — called by load_all_skills(registry)
# ---------------------------------------------------------------------------
def register(registry: SkillRegistry) -> None:
    """Register FileStreamerSkill under the canonical skill name."""
    registry.register("file_streamer", FileStreamerSkill())