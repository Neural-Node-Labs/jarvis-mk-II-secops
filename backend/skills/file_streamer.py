"""
FileStreamerSkill - Handles large file writing using segmented chunk appending.

Resolves LLM max_token truncation by splitting file writes across multiple
agent turns: start_file → append_chunk (×N) → finalize_file.

CBD Contract:
  IN:  action ∈ {start_file, append_chunk, finalize_file}, params dict
  OUT: SkillResult(success, output, error)

Process-safety: stateless — uses only disk-level checks (os.path.exists),
no in-memory sets or instance-level tracking. Safe across OS worker processes.

Fixes applied (v1.1.0):
  [FIX-1] Atomic file creation via open(path, "x") eliminates the
          check-then-act race condition under concurrent FastAPI workers.
  [FIX-2] Sentinel orphan prevention: target file is cleaned up if sentinel
          creation fails mid-way through _start_file.
  [FIX-3] finalize_file now hard-errors when the target file is missing
          instead of silently reporting 0 bytes.
  [FIX-4] append_chunk rejects non-str content and warns on empty strings.
"""

import os
import logging
from core.skill_registry import SkillRegistry, SkillResult

logger = logging.getLogger("skill.file_streamer")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SENTINEL_SUFFIX = ".fstream_open"   # Marker file that signals an active stream


def _sentinel_path(path: str) -> str:
    """Return the path of the sentinel marker for *path*."""
    return path + _SENTINEL_SUFFIX


def _ensure_parent_dir(path: str) -> None:
    """Create parent directories if they do not exist."""
    parent = os.path.dirname(path)
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
          path      (str,  required) — destination path, relative or absolute
          overwrite (bool, optional) — default False; if False and file exists,
                                       returns an error rather than clobbering
        OUT output:
          {"path": str, "status": "stream_opened", "bytes_written": 0}

        FIX-1: Uses open(path, "x") for exclusive creation when overwrite=False
               so the existence check and file creation are a single atomic OS
               operation, eliminating the TOCTOU race condition.
        FIX-2: If sentinel creation fails after the target file is created,
               the target file is removed to prevent a permanently broken state.
        """
        path = params.get("path")
        if not path:
            return SkillResult(False, None, error="'path' is required for start_file.")

        overwrite: bool = bool(params.get("overwrite", False))

        # Guard: another stream already open for this path
        sentinel = _sentinel_path(path)
        if os.path.exists(sentinel):
            return SkillResult(
                False, None,
                error=(
                    f"A stream is already open for '{path}'. "
                    "Call finalize_file to close it before starting a new one."
                )
            )

        try:
            _ensure_parent_dir(path)

            # FIX-1: atomic exclusive-create when overwrite=False.
            # open("x") raises FileExistsError if the file is already present,
            # so no separate os.path.exists() check is needed — eliminating the
            # check-then-act race that existed in the original code.
            file_mode = "w" if overwrite else "x"
            try:
                with open(path, file_mode, encoding="utf-8") as fh:
                    fh.write("")  # empty — content comes via append_chunk
            except FileExistsError:
                return SkillResult(
                    False, None,
                    error=(
                        f"File '{path}' already exists. "
                        "Set overwrite=true to replace it."
                    )
                )

            # FIX-2: create sentinel in a separate try so we can roll back the
            # target file if the sentinel write fails (e.g. disk full), which
            # would otherwise leave a truncated file with no way to finalize it.
            try:
                with open(sentinel, "w", encoding="utf-8") as fh:
                    fh.write(path)
            except OSError as exc:
                # Roll back: remove the just-created target file
                try:
                    os.remove(path)
                except OSError:
                    pass
                return SkillResult(
                    False, None,
                    error=f"start_file failed creating sentinel (rolled back target): {exc}"
                )

            logger.info("Stream opened: %s", path)
            return SkillResult(
                True,
                output={
                    "path": path,
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
          path    (str, required) — must match a previously started stream
          content (str, required) — text chunk to append (no size limit)
        OUT output:
          {"path": str, "status": "chunk_appended", "chunk_bytes": int,
           "total_bytes": int}

        FIX-4: Explicitly rejects non-string content and warns when an empty
               string is passed, so callers are not silently misled.
        """
        path = params.get("path")
        if not path:
            return SkillResult(False, None, error="'path' is required for append_chunk.")

        content = params.get("content")

        # FIX-4a: content must be present and must be a string.
        if content is None:
            return SkillResult(False, None, error="'content' is required for append_chunk.")
        if not isinstance(content, str):
            return SkillResult(
                False, None,
                error=(
                    f"'content' must be a string, got {type(content).__name__}. "
                    "Encode binary data before passing."
                )
            )
        # FIX-4b: warn (but do not fail) when content is an empty string so the
        # caller can detect a likely logic error without hard-failing the stream.
        if content == "":
            logger.warning("append_chunk called with empty content for '%s' — no bytes written.", path)

        sentinel = _sentinel_path(path)
        if not os.path.exists(sentinel):
            return SkillResult(
                False, None,
                error=(
                    f"No open stream found for '{path}'. "
                    "Call start_file first."
                )
            )

        if not os.path.exists(path):
            return SkillResult(
                False, None,
                error=f"Target file '{path}' is missing — stream may be corrupt."
            )

        try:
            chunk_bytes = len(content.encode("utf-8"))
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(content)

            total_bytes = os.path.getsize(path)
            logger.info(
                "Chunk appended to %s (%d bytes, total %d)",
                path, chunk_bytes, total_bytes,
            )
            return SkillResult(
                True,
                output={
                    "path": path,
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
          path (str, required) — path of the stream to close
        OUT output:
          {"path": str, "status": "stream_finalized", "total_bytes": int}

        FIX-3: Hard-errors when the target file is absent at finalization time
               instead of silently returning total_bytes=0, which masked data
               loss caused by external deletion or a corrupt stream.
        """
        path = params.get("path")
        if not path:
            return SkillResult(False, None, error="'path' is required for finalize_file.")

        sentinel = _sentinel_path(path)
        if not os.path.exists(sentinel):
            return SkillResult(
                False, None,
                error=(
                    f"No open stream found for '{path}'. "
                    "Either it was already finalized or start_file was never called."
                )
            )

        # FIX-3: Verify the target file still exists before declaring success.
        # The original code silently returned total_bytes=0 when the file was
        # missing, giving the caller a false "success" signal and hiding data loss.
        if not os.path.exists(path):
            # Clean up the orphaned sentinel so the path is not permanently locked.
            try:
                os.remove(sentinel)
            except OSError:
                pass
            return SkillResult(
                False, None,
                error=(
                    f"Target file '{path}' is missing at finalization — data may have been lost. "
                    "Sentinel has been removed to unlock the path."
                )
            )

        try:
            total_bytes = os.path.getsize(path)
            os.remove(sentinel)
            logger.info("Stream finalized: %s (%d bytes)", path, total_bytes)
            return SkillResult(
                True,
                output={
                    "path": path,
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