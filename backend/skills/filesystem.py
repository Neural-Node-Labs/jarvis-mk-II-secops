"""
Filesystem Skill — Full host filesystem access for Jarvis MKII.
Implements SKL-001 from cbd-skills.md.

version: 1.0.0
changelog:
  1.0.0 - 2026-06-10 - Initial implementation. Actions: read_file, write_file,
                        list_dir, delete, move, mkdir, search_files, stat.
                        Destructive actions gated with requires_confirm.
                        Atomic writes via temp-file rename for critical files.
"""
import os
import json
import shutil
import fnmatch
import logging
import tempfile
import traceback
from datetime import datetime, timezone
from core.skill_registry import SkillResult

logger = logging.getLogger("skill.filesystem")

# Files that must use atomic write (temp → rename)
ATOMIC_WRITE_PATTERNS = {"blueprint.json", "index.md", "skills_manifest.json"}

# Actions that require user confirmation
DESTRUCTIVE_ACTIONS = {"delete", "move"}


class FilesystemSkill:

    async def execute(self, action: str, params: dict, confirmed: bool = False) -> SkillResult:
        try:
            dispatch = {
                "read_file":    self._read_file,
                "write_file":   self._write_file,
                "list_dir":     self._list_dir,
                "delete":       self._delete,
                "move":         self._move,
                "mkdir":        self._mkdir,
                "search_files": self._search_files,
                "stat":         self._stat,
            }
            fn = dispatch.get(action)
            if fn is None:
                return SkillResult.fail(f"FS_UNKNOWN_ACTION: '{action}'. Valid: {', '.join(dispatch)}")
            return await fn(params, confirmed)
        except Exception as exc:
            logger.error("[filesystem.%s] %s\n%s", action, exc, traceback.format_exc())
            return SkillResult.fail(f"FS_INTERNAL_ERROR: {type(exc).__name__}: {exc}")

    # ── Read ───────────────────────────────────────────────────────────────────

    async def _read_file(self, params: dict, confirmed: bool) -> SkillResult:
        path     = params.get("path", "")
        encoding = params.get("encoding", "utf-8")
        max_bytes = int(params.get("max_bytes", 512_000))  # 512 KB default cap

        if not path:
            return SkillResult.fail("FS_MISSING_PARAM: path is required")
        if not os.path.isfile(path):
            return SkillResult.fail(f"FS_NOT_FOUND: no file at '{path}'")

        try:
            size = os.path.getsize(path)
            with open(path, "r", encoding=encoding, errors="replace") as f:
                content = f.read(max_bytes)

            truncated = size > max_bytes
            logger.info("[fs.read_file] path=%s size=%d truncated=%s", path, size, truncated)
            return SkillResult.ok({
                "path":      path,
                "content":   content,
                "size":      size,
                "truncated": truncated,
                "encoding":  encoding,
            })
        except Exception as exc:
            return SkillResult.fail(f"FS_READ_ERROR: {exc}")

    # ── Write ──────────────────────────────────────────────────────────────────

    async def _write_file(self, params: dict, confirmed: bool) -> SkillResult:
        path     = params.get("path", "")
        content  = params.get("content", "")
        encoding = params.get("encoding", "utf-8")
        overwrite = params.get("overwrite", True)

        if not path:
            return SkillResult.fail("FS_MISSING_PARAM: path is required")

        exists = os.path.isfile(path)
        if exists and not overwrite:
            return SkillResult.fail(f"FS_EXISTS: '{path}' already exists and overwrite=false")

        # Gate writes to sensitive paths
        if not confirmed and _is_sensitive(path) and exists:
            return SkillResult.confirm(
                f"About to overwrite '{path}' ({os.path.getsize(path)} bytes). Confirm?",
                output={"path": path},
            )

        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

            # Atomic write for critical files
            if os.path.basename(path) in ATOMIC_WRITE_PATTERNS:
                _atomic_write(path, content, encoding)
            else:
                with open(path, "w", encoding=encoding) as f:
                    f.write(content)

            size = os.path.getsize(path)
            logger.info("[fs.write_file] path=%s size=%d", path, size)
            return SkillResult.ok({"path": path, "size": size, "written_at": _now()})
        except Exception as exc:
            return SkillResult.fail(f"FS_WRITE_ERROR: {exc}")

    # ── List dir ───────────────────────────────────────────────────────────────

    async def _list_dir(self, params: dict, confirmed: bool) -> SkillResult:
        path      = params.get("path", ".")
        recursive = params.get("recursive", False)
        pattern   = params.get("pattern", "*")

        if not os.path.isdir(path):
            return SkillResult.fail(f"FS_NOT_DIR: '{path}' is not a directory")

        try:
            entries = []
            if recursive:
                for root, dirs, files in os.walk(path):
                    # Skip hidden dirs
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    for name in files:
                        if fnmatch.fnmatch(name, pattern):
                            full = os.path.join(root, name)
                            entries.append({
                                "path": full,
                                "type": "file",
                                "size": os.path.getsize(full),
                            })
                    for name in dirs:
                        entries.append({"path": os.path.join(root, name), "type": "dir"})
            else:
                for name in sorted(os.listdir(path)):
                    full = os.path.join(path, name)
                    entry = {
                        "name": name,
                        "path": full,
                        "type": "dir" if os.path.isdir(full) else "file",
                    }
                    if entry["type"] == "file":
                        entry["size"] = os.path.getsize(full)
                    entries.append(entry)

            logger.info("[fs.list_dir] path=%s entries=%d", path, len(entries))
            return SkillResult.ok({"path": path, "entries": entries, "count": len(entries)})
        except Exception as exc:
            return SkillResult.fail(f"FS_LIST_ERROR: {exc}")

    # ── Delete ─────────────────────────────────────────────────────────────────

    async def _delete(self, params: dict, confirmed: bool) -> SkillResult:
        path = params.get("path", "")
        if not path:
            return SkillResult.fail("FS_MISSING_PARAM: path is required")

        if not os.path.exists(path):
            return SkillResult.fail(f"FS_NOT_FOUND: '{path}' does not exist")

        if not confirmed:
            size_info = f"{os.path.getsize(path)} bytes" if os.path.isfile(path) else "directory"
            return SkillResult.confirm(
                f"⚠️ About to DELETE '{path}' ({size_info}). This cannot be undone. Confirm?",
                output={"path": path},
            )

        try:
            if os.path.isfile(path):
                os.remove(path)
            else:
                shutil.rmtree(path)
            logger.info("[fs.delete] path=%s", path)
            return SkillResult.ok({"deleted": path, "deleted_at": _now()})
        except Exception as exc:
            return SkillResult.fail(f"FS_DELETE_ERROR: {exc}")

    # ── Move ───────────────────────────────────────────────────────────────────

    async def _move(self, params: dict, confirmed: bool) -> SkillResult:
        src = params.get("src", params.get("source", ""))
        dst = params.get("dst", params.get("destination", ""))
        if not src or not dst:
            return SkillResult.fail("FS_MISSING_PARAM: src and dst are required")

        if not os.path.exists(src):
            return SkillResult.fail(f"FS_NOT_FOUND: '{src}' does not exist")

        if not confirmed:
            return SkillResult.confirm(
                f"⚠️ About to MOVE '{src}' → '{dst}'. Confirm?",
                output={"src": src, "dst": dst},
            )

        try:
            os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
            shutil.move(src, dst)
            logger.info("[fs.move] %s → %s", src, dst)
            return SkillResult.ok({"moved": True, "src": src, "dst": dst})
        except Exception as exc:
            return SkillResult.fail(f"FS_MOVE_ERROR: {exc}")

    # ── Mkdir ──────────────────────────────────────────────────────────────────

    async def _mkdir(self, params: dict, confirmed: bool) -> SkillResult:
        path = params.get("path", "")
        if not path:
            return SkillResult.fail("FS_MISSING_PARAM: path is required")
        try:
            os.makedirs(path, exist_ok=True)
            logger.info("[fs.mkdir] path=%s", path)
            return SkillResult.ok({"created": path})
        except Exception as exc:
            return SkillResult.fail(f"FS_MKDIR_ERROR: {exc}")

    # ── Search files ───────────────────────────────────────────────────────────

    async def _search_files(self, params: dict, confirmed: bool) -> SkillResult:
        root        = params.get("path", ".")
        pattern     = params.get("pattern", "*")
        contains    = params.get("contains", "")
        max_results = int(params.get("max_results", 100))

        if not os.path.isdir(root):
            return SkillResult.fail(f"FS_NOT_DIR: '{root}' is not a directory")

        try:
            matches = []
            for dirpath, dirs, files in os.walk(root):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("__pycache__", "node_modules", ".git")]
                for name in files:
                    if fnmatch.fnmatch(name, pattern):
                        full = os.path.join(dirpath, name)
                        if contains:
                            try:
                                with open(full, "r", encoding="utf-8", errors="ignore") as f:
                                    if contains.lower() not in f.read().lower():
                                        continue
                            except Exception:
                                continue
                        matches.append({"path": full, "name": name, "size": os.path.getsize(full)})
                        if len(matches) >= max_results:
                            break
                if len(matches) >= max_results:
                    break

            logger.info("[fs.search_files] root=%s pattern=%s found=%d", root, pattern, len(matches))
            return SkillResult.ok({"matches": matches, "count": len(matches), "truncated": len(matches) >= max_results})
        except Exception as exc:
            return SkillResult.fail(f"FS_SEARCH_ERROR: {exc}")

    # ── Stat ───────────────────────────────────────────────────────────────────

    async def _stat(self, params: dict, confirmed: bool) -> SkillResult:
        path = params.get("path", "")
        if not path:
            return SkillResult.fail("FS_MISSING_PARAM: path is required")
        if not os.path.exists(path):
            return SkillResult.fail(f"FS_NOT_FOUND: '{path}'")
        try:
            s = os.stat(path)
            return SkillResult.ok({
                "path":     path,
                "type":     "dir" if os.path.isdir(path) else "file",
                "size":     s.st_size,
                "modified": datetime.fromtimestamp(s.st_mtime, tz=timezone.utc).isoformat(),
                "created":  datetime.fromtimestamp(s.st_ctime, tz=timezone.utc).isoformat(),
                "mode":     oct(s.st_mode),
            })
        except Exception as exc:
            return SkillResult.fail(f"FS_STAT_ERROR: {exc}")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _is_sensitive(path: str) -> bool:
    """Flag paths that warrant extra caution."""
    sensitive = {"/etc", "/usr", "/bin", "/sbin", "/boot", "/sys", "/proc"}
    return any(os.path.abspath(path).startswith(s) for s in sensitive)


def _atomic_write(path: str, content: str, encoding: str = "utf-8"):
    """Write content to a temp file then rename — prevents partial corruption."""
    dir_ = os.path.dirname(os.path.abspath(path)) or "."
    with tempfile.NamedTemporaryFile("w", dir=dir_, encoding=encoding, delete=False, suffix=".tmp") as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    os.replace(tmp_path, path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
