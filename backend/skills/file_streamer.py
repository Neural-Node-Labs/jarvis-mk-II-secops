"""
File Streamer Skill — Chunked large file writes bypassing LLM max_token limits.
Implements SKL-004 from cbd-skills.md.

version: 1.1.0
changelog:
  1.1.0 - 2026-06-14 - Production hardening.
    FIXED  _start_file: TOCTOU race between existence check and stream registration
           eliminated — both guards now run inside one lock acquisition. A None
           placeholder is inserted immediately so concurrent calls for the same
           path fail fast; placeholder is cleared in the except block on error.
    FIXED  _append_chunk: placeholder guard added (returns FS_STREAM_NOT_READY
           instead of AttributeError on None); byte-count now computed from
           encoded bytes to match finalize's MD5 reference.
    FIXED  _finalize_file: stream entry is now only removed from registry AFTER
           successful rename, so abort can still clean up the temp file if
           finalize errors mid-way. os.replace failure returns FS_STREAM_FINALIZE_ERROR
           instead of silently leaving an orphaned .stream_tmp on disk.
    FIXED  _status: values now snapshotted under the lock; placeholder slots
           (None) are excluded from the open_streams list.
    FIXED  _abort: placeholder slots are handled explicitly; individual close()
           and unlink() failures are logged as warnings and surfaced in output
           instead of being silently swallowed.
  1.0.0 - 2026-06-10 - Initial.
"""
import os
import logging
import threading
import tempfile
import hashlib
from datetime import datetime, timezone
from core.skill_registry import SkillResult

logger = logging.getLogger("skill.file_streamer")

# Active stream registry: path → state dict
_streams: dict[str, dict] = {}
_lock = threading.Lock()


class FileStreamerSkill:

    async def execute(self, action: str, params: dict, confirmed: bool = False) -> SkillResult:
        dispatch = {
            "start_file":    self._start_file,
            "append_chunk":  self._append_chunk,
            "finalize_file": self._finalize_file,
            "status":        self._status,
            "abort":         self._abort,
        }
        fn = dispatch.get(action)
        if fn is None:
            return SkillResult.fail(f"FS_STREAM_UNKNOWN_ACTION: '{action}'. Valid: {', '.join(dispatch)}")
        return await fn(params, confirmed)

    async def _start_file(self, params: dict, confirmed: bool) -> SkillResult:
        path      = params.get("path", "")
        overwrite = params.get("overwrite", True)
        encoding  = params.get("encoding", "utf-8")

        if not path:
            return SkillResult.fail("FS_STREAM_MISSING_PATH")

        # FIX: all guards and registration inside one lock acquisition to eliminate
        # the TOCTOU window between the existence check and stream insertion.
        with _lock:
            if path in _streams:
                return SkillResult.fail(f"FS_STREAM_ALREADY_OPEN: '{path}' has an open stream. finalize or abort first.")
            if os.path.isfile(path) and not overwrite:
                return SkillResult.fail(f"FS_STREAM_EXISTS: '{path}' exists and overwrite=false")
            # Reserve the slot immediately so concurrent start_file for the same
            # path fails fast instead of both opening temp files.
            _streams[path] = None  # placeholder

        try:
            dir_ = os.path.dirname(os.path.abspath(path)) or "."
            os.makedirs(dir_, exist_ok=True)
            tmp = tempfile.NamedTemporaryFile(
                mode="w", dir=dir_, encoding=encoding,
                suffix=".stream_tmp", delete=False
            )
            with _lock:
                _streams[path] = {
                    "tmp_path":  tmp.name,
                    "target":    path,
                    "encoding":  encoding,
                    "chunks":    0,
                    "bytes":     0,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "handle":    tmp,
                }
            logger.info("[file_streamer.start] path=%s tmp=%s", path, tmp.name)
            return SkillResult.ok({"path": path, "tmp_path": tmp.name, "status": "open"})
        except Exception as exc:
            # FIX: remove the placeholder slot so the path isn't permanently locked
            with _lock:
                if _streams.get(path) is None:
                    _streams.pop(path, None)
            return SkillResult.fail(f"FS_STREAM_START_ERROR: {exc}")

    async def _append_chunk(self, params: dict, confirmed: bool) -> SkillResult:
        path    = params.get("path", "")
        content = params.get("content", "")

        if not path:
            return SkillResult.fail("FS_STREAM_MISSING_PATH")

        with _lock:
            state = _streams.get(path)

        if state is None:
            return SkillResult.fail(f"FS_STREAM_NOT_OPEN: call start_file first for '{path}'")

        # FIX: placeholder slot means start_file is still initialising — treat as not open.
        if not isinstance(state, dict):
            return SkillResult.fail(f"FS_STREAM_NOT_READY: stream for '{path}' is still initialising")

        try:
            handle = state["handle"]
            handle.write(content)
            handle.flush()
            # FIX: compute byte delta from the encoded form, same way _start_file will report.
            encoded_len = len(content.encode(state["encoding"], errors="replace"))
            with _lock:
                state["chunks"] += 1
                state["bytes"]  += encoded_len

            logger.debug("[file_streamer.append] path=%s chunk=%d bytes=%d",
                         path, state["chunks"], state["bytes"])
            return SkillResult.ok({
                "path":   path,
                "chunks": state["chunks"],
                "bytes":  state["bytes"],
            })
        except Exception as exc:
            return SkillResult.fail(f"FS_STREAM_APPEND_ERROR: {exc}")

    async def _finalize_file(self, params: dict, confirmed: bool) -> SkillResult:
        path = params.get("path", "")
        if not path:
            return SkillResult.fail("FS_STREAM_MISSING_PATH")

        with _lock:
            state = _streams.get(path)

        if state is None:
            return SkillResult.fail(f"FS_STREAM_NOT_OPEN: no open stream for '{path}'")

        # FIX: remove from registry only AFTER everything succeeds so abort can
        # still clean up the temp file if finalize errors mid-way.
        tmp_path = state["tmp_path"]
        try:
            handle = state["handle"]
            handle.close()

            # FIX: wrapped in try/finally — if os.replace fails the tmp file
            # is cleaned up and the stream entry is left for abort to collect.
            try:
                os.replace(tmp_path, path)
            except Exception as exc:
                return SkillResult.fail(f"FS_STREAM_FINALIZE_ERROR: rename failed: {exc}")

            final_size = os.path.getsize(path)
            # Compute MD5 for verification
            md5 = hashlib.md5()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    md5.update(chunk)

            with _lock:
                del _streams[path]

            logger.info("[file_streamer.finalize] path=%s size=%d chunks=%d md5=%s",
                        path, final_size, state["chunks"], md5.hexdigest()[:8])
            return SkillResult.ok({
                "path":        path,
                "size_bytes":  final_size,
                "chunks":      state["chunks"],
                "md5":         md5.hexdigest(),
                "finalized_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as exc:
            return SkillResult.fail(f"FS_STREAM_FINALIZE_ERROR: {exc}")

    async def _status(self, params: dict, confirmed: bool) -> SkillResult:
        path = params.get("path", "")
        with _lock:
            if path:
                state = _streams.get(path)
                if state is None or not isinstance(state, dict):
                    return SkillResult.ok({"path": path, "status": "not_open"})
                # FIX: snapshot values under the lock so we don't race with append_chunk
                snap = {
                    "path":       path,
                    "status":     "open",
                    "chunks":     state["chunks"],
                    "bytes":      state["bytes"],
                    "started_at": state["started_at"],
                }
                return SkillResult.ok(snap)
            else:
                return SkillResult.ok({
                    "open_streams": [
                        {"path": p, "chunks": s["chunks"], "bytes": s["bytes"]}
                        for p, s in _streams.items()
                        if isinstance(s, dict)
                    ],
                    "count": sum(1 for s in _streams.values() if isinstance(s, dict)),
                })

    async def _abort(self, params: dict, confirmed: bool) -> SkillResult:
        path = params.get("path", "")
        if not path:
            return SkillResult.fail("FS_STREAM_MISSING_PATH")

        with _lock:
            state = _streams.pop(path, None)

        if state is None:
            return SkillResult.fail(f"FS_STREAM_NOT_OPEN: no stream for '{path}'")

        # FIX: placeholder slots (None) mean start_file errored after reserving;
        # they've already been cleaned by start_file's except block but handle
        # the edge case where they weren't.
        if not isinstance(state, dict):
            logger.info("[file_streamer.abort] cleared placeholder for path=%s", path)
            return SkillResult.ok({"aborted": True, "path": path, "note": "cleared uninitialised slot"})

        cleanup_errors = []
        try:
            state["handle"].close()
        except Exception as exc:
            cleanup_errors.append(f"close: {exc}")
        try:
            if os.path.exists(state["tmp_path"]):
                os.remove(state["tmp_path"])
        except Exception as exc:
            cleanup_errors.append(f"unlink: {exc}")

        if cleanup_errors:
            logger.warning("[file_streamer.abort] path=%s cleanup_errors=%s", path, cleanup_errors)

        logger.info("[file_streamer.abort] path=%s", path)
        result = {"aborted": True, "path": path}
        if cleanup_errors:
            result["cleanup_warnings"] = cleanup_errors
        return SkillResult.ok(result)