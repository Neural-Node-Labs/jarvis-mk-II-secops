"""
File Streamer Skill — Chunked large file writes bypassing LLM max_token limits.
Implements SKL-004 from cbd-skills.md.

version: 1.0.0
changelog:
  1.0.0 - 2026-06-10 - Initial. Actions: start_file, append_chunk, finalize_file, status.
                        Thread-safe per-path state tracking.
                        Atomic finalize via temp-file rename.
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

        with _lock:
            if path in _streams:
                return SkillResult.fail(f"FS_STREAM_ALREADY_OPEN: '{path}' has an open stream. finalize or abort first.")

        if os.path.isfile(path) and not overwrite:
            return SkillResult.fail(f"FS_STREAM_EXISTS: '{path}' exists and overwrite=false")

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

        try:
            handle = state["handle"]
            handle.write(content)
            handle.flush()
            with _lock:
                state["chunks"] += 1
                state["bytes"]  += len(content.encode(state["encoding"], errors="replace"))

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

        try:
            handle = state["handle"]
            handle.close()
            os.replace(state["tmp_path"], path)

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
                if state is None:
                    return SkillResult.ok({"path": path, "status": "not_open"})
                return SkillResult.ok({
                    "path":       path,
                    "status":     "open",
                    "chunks":     state["chunks"],
                    "bytes":      state["bytes"],
                    "started_at": state["started_at"],
                })
            else:
                return SkillResult.ok({
                    "open_streams": [
                        {"path": p, "chunks": s["chunks"], "bytes": s["bytes"]}
                        for p, s in _streams.items()
                    ],
                    "count": len(_streams),
                })

    async def _abort(self, params: dict, confirmed: bool) -> SkillResult:
        path = params.get("path", "")
        if not path:
            return SkillResult.fail("FS_STREAM_MISSING_PATH")

        with _lock:
            state = _streams.pop(path, None)

        if state is None:
            return SkillResult.fail(f"FS_STREAM_NOT_OPEN: no stream for '{path}'")

        try:
            state["handle"].close()
            if os.path.exists(state["tmp_path"]):
                os.remove(state["tmp_path"])
        except Exception:
            pass

        logger.info("[file_streamer.abort] path=%s", path)
        return SkillResult.ok({"aborted": True, "path": path})
