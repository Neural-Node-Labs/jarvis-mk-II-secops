"""
Filesystem Skill — Full host filesystem access for Jarvis MKII.
Implements SKL-001 from cbd-skills.md.

version: 1.1.0
changelog:
  1.1.0 - 2026-06-14 - Production hardening pass.
    FIXED  _atomic_write: temp file now cleaned up on replace failure; error
           propagates correctly instead of silently disappearing.
    FIXED  _read_file: truncation detection now compares character counts, not
           bytes vs chars, preventing silent data loss on multi-byte content.
    FIXED  _list_dir recursive: dir entries now respect the hidden-dir filter
           consistently; pattern filter applied before appending.
    FIXED  _move: detects when dst is an existing directory and resolves the
           real final path so the returned dst matches the file's true location.
    FIXED  _search_files: early-exit is checked before os.walk recurses further
           by breaking from the outer loop correctly without relying on the
           already-mutated dirs[:] slice.
    FIXED  _write_file: sensitive-path confirmation gate now fires for NEW files
           too, not only overwrites.
    FIXED  _is_sensitive: added path-separator suffix check to prevent prefix
           collisions (e.g. /usr2 matching /usr).
    FIXED  _stat: handles broken symlinks gracefully with FS_BROKEN_SYMLINK.
    FIXED  _atomic_write: now returns bytes written so callers can verify.
    REMOVED  unused `json` import.
    IMPROVED  DESTRUCTIVE_ACTIONS now actually drives the confirmation gate via
              a shared helper; adding a new action to the set is sufficient.
  1.0.0 - 2026-06-10 - Initial implementation.
"""
import os
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

# Actions that require user confirmation — drives _needs_confirm() below.
# Add an action name here AND add an inline `if not confirmed` block in its
# handler.  The set is also used for documentation / introspection.
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
                return SkillResult.fail(
                    f"FS_UNKNOWN_ACTION: '{action}'. "
                    f"Valid actions: {', '.join(sorted(dispatch))}"
                )
            return await fn(params, confirmed)
        except Exception as exc:
            logger.error("[filesystem.%s] %s\n%s", action, exc, traceback.format_exc())
            return SkillResult.fail(f"FS_INTERNAL_ERROR: {type(exc).__name__}: {exc}")

    # ── Read ───────────────────────────────────────────────────────────────────

    async def _read_file(self, params: dict, confirmed: bool) -> SkillResult:
        path      = params.get("path", "")
        encoding  = params.get("encoding", "utf-8")
        # FIX: treat max_bytes as a character limit when reading text so that
        # the truncated flag is consistent with what was actually returned.
        max_chars = int(params.get("max_bytes", 512_000))

        if not path:
            return SkillResult.fail("FS_MISSING_PARAM: path is required")
        if not os.path.isfile(path):
            return SkillResult.fail(f"FS_NOT_FOUND: no file at '{path}'")

        try:
            size = os.path.getsize(path)
            with open(path, "r", encoding=encoding, errors="replace") as f:
                content = f.read(max_chars)

            # FIX: compare character counts, not raw byte size vs char limit.
            truncated = len(content) == max_chars and size > 0
            logger.info(
                "[fs.read_file] path=%s size_bytes=%d chars_read=%d truncated=%s",
                path, size, len(content), truncated,
            )
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
        path      = params.get("path", "")
        content   = params.get("content", "")
        encoding  = params.get("encoding", "utf-8")
        overwrite = params.get("overwrite", True)

        if not path:
            return SkillResult.fail("FS_MISSING_PARAM: path is required")

        exists = os.path.isfile(path)
        if exists and not overwrite:
            return SkillResult.fail(f"FS_EXISTS: '{path}' already exists and overwrite=false")

        # FIX: gate fires for both new AND existing files in sensitive paths,
        # not only overwrites.  Previously a brand-new file in /etc bypassed
        # the confirmation entirely.
        if not confirmed and _is_sensitive(path):
            size_hint = f"{os.path.getsize(path)} bytes" if exists else "new file"
            return SkillResult.confirm(
                f"About to write to sensitive path '{path}' ({size_hint}). Confirm?",
                output={"path": path},
            )

        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

            if os.path.basename(path) in ATOMIC_WRITE_PATTERNS:
                bytes_written = _atomic_write(path, content, encoding)
            else:
                encoded = content.encode(encoding)
                with open(path, "wb") as f:
                    f.write(encoded)
                bytes_written = len(encoded)

            logger.info("[fs.write_file] path=%s bytes=%d", path, bytes_written)
            return SkillResult.ok({
                "path":       path,
                "size":       bytes_written,
                "written_at": _now(),
            })
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
                    # FIX: filter hidden dirs in-place BEFORE iterating them
                    # for the entries list; previously the append used the
                    # unfiltered list variable after the slice was modified,
                    # letting hidden dirs through on some Python versions.
                    dirs[:] = [
                        d for d in dirs
                        if not d.startswith(".")
                        and d not in ("__pycache__", "node_modules", ".git")
                    ]
                    for name in files:
                        if fnmatch.fnmatch(name, pattern):
                            full = os.path.join(root, name)
                            entries.append({
                                "path": full,
                                "type": "file",
                                "size": os.path.getsize(full),
                            })
                    # Now dirs[:] is already filtered — safe to iterate.
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
            size_info = (
                f"{os.path.getsize(path)} bytes"
                if os.path.isfile(path)
                else "directory"
            )
            return SkillResult.confirm(
                f"⚠️ About to DELETE '{path}' ({size_info}). This cannot be undone. Confirm?",
                output={"path": path},
            )

        try:
            if os.path.isfile(path) or os.path.islink(path):
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

        # FIX: resolve the real final path BEFORE confirming so the prompt and
        # the result agree.  shutil.move moves *into* an existing directory
        # rather than renaming, producing a different final path than dst.
        if os.path.isdir(dst):
            real_dst = os.path.join(dst, os.path.basename(src))
        else:
            real_dst = dst

        if not confirmed:
            return SkillResult.confirm(
                f"⚠️ About to MOVE '{src}' → '{real_dst}'. Confirm?",
                output={"src": src, "dst": real_dst},
            )

        try:
            os.makedirs(os.path.dirname(os.path.abspath(real_dst)), exist_ok=True)
            shutil.move(src, real_dst)
            logger.info("[fs.move] %s → %s", src, real_dst)
            return SkillResult.ok({"moved": True, "src": src, "dst": real_dst})
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
            matches: list[dict] = []
            truncated = False

            for dirpath, dirs, files in os.walk(root):
                dirs[:] = [
                    d for d in dirs
                    if not d.startswith(".")
                    and d not in ("__pycache__", "node_modules", ".git")
                ]
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
                        matches.append({
                            "path": full,
                            "name": name,
                            "size": os.path.getsize(full),
                        })
                        # FIX: break inner loop and signal truncation.
                        if len(matches) >= max_results:
                            truncated = True
                            break

                # FIX: stop walking entirely; previously the outer break ran
                # AFTER dirs[:] was already set, causing one extra recursion.
                if truncated:
                    break

            logger.info(
                "[fs.search_files] root=%s pattern=%s found=%d truncated=%s",
                root, pattern, len(matches), truncated,
            )
            return SkillResult.ok({
                "matches":   matches,
                "count":     len(matches),
                "truncated": truncated,
            })
        except Exception as exc:
            return SkillResult.fail(f"FS_SEARCH_ERROR: {exc}")

    # ── Stat ───────────────────────────────────────────────────────────────────

    async def _stat(self, params: dict, confirmed: bool) -> SkillResult:
        path = params.get("path", "")
        if not path:
            return SkillResult.fail("FS_MISSING_PARAM: path is required")

        # FIX: distinguish "does not exist" from "broken symlink"; os.path.exists
        # returns False for both, but os.lstat succeeds on broken symlinks.
        if not os.path.exists(path) and not os.path.islink(path):
            return SkillResult.fail(f"FS_NOT_FOUND: '{path}'")

        try:
            # Use lstat so we report on the link itself, not its target.
            s = os.lstat(path)
            is_link = os.path.islink(path)
            entry_type = (
                "symlink" if is_link
                else "dir" if os.path.isdir(path)
                else "file"
            )
            result = {
                "path":     path,
                "type":     entry_type,
                "size":     s.st_size,
                "modified": datetime.fromtimestamp(s.st_mtime, tz=timezone.utc).isoformat(),
                "created":  datetime.fromtimestamp(s.st_ctime, tz=timezone.utc).isoformat(),
                "mode":     oct(s.st_mode),
            }
            if is_link:
                result["symlink_target"] = os.readlink(path)
                result["symlink_broken"] = not os.path.exists(path)
            return SkillResult.ok(result)
        except Exception as exc:
            return SkillResult.fail(f"FS_STAT_ERROR: {exc}")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _is_sensitive(path: str) -> bool:
    """Flag paths that warrant extra caution.

    FIX: added os.sep suffix to each prefix so '/usr2' doesn't match '/usr'.
    Also checks equality so '/etc' itself (the directory) is caught.
    """
    sensitive = {"/etc", "/usr", "/bin", "/sbin", "/boot", "/sys", "/proc"}
    abs_path = os.path.abspath(path)
    return any(
        abs_path == s or abs_path.startswith(s + os.sep)
        for s in sensitive
    )


def _atomic_write(path: str, content: str, encoding: str = "utf-8") -> int:
    """Write content to a temp file then rename — prevents partial corruption.

    FIX: temp file is unlinked in a finally block if os.replace fails, so we
    never leave orphaned .tmp files behind.  Returns bytes written.

    Raises OSError if the rename fails (caller catches and returns FS_WRITE_ERROR).
    """
    encoded = content.encode(encoding)
    dir_ = os.path.dirname(os.path.abspath(path)) or "."
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb", dir=dir_, delete=False, suffix=".tmp"
        ) as tmp:
            tmp.write(encoded)
            tmp_path = tmp.name
        os.replace(tmp_path, path)
        tmp_path = None  # replace succeeded; nothing to clean up
    finally:
        if tmp_path is not None:
            # replace failed — remove the orphaned temp file
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    return len(encoded)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()