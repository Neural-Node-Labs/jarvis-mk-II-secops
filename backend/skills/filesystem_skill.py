"""
SKILL: FileSystem
CBD Identity:
  Name: FileSystemSkill
  Reason: Give the agent full read/write/delete access to the host filesystem.
  Logical Function: I/O, Storage

IN-Schema:  { action, path, content?, recursive?, pattern? }
OUT-Schema: { success, output, error, requires_confirm, confirm_prompt }
Error-Schema: { success: false, error: "<message>" }

Safety Model: CONFIRM before write, delete, overwrite actions.
"""
import os
import glob
import shutil
import aiofiles
import logging
from pathlib import Path
from core.skill_registry import SkillResult

logger = logging.getLogger("skill.filesystem")

DESTRUCTIVE_ACTIONS = {"write_file", "delete", "move", "mkdir_force"}


class FileSystemSkill:
    description = "Full host filesystem access: read, write, list, delete, move, search files."
    actions = [
        "read_file", "write_file", "list_dir", "delete",
        "move", "mkdir", "search_files", "stat",
    ]

    async def execute(self, action: str, params: dict, confirmed: bool = False) -> SkillResult:
        # Safety gate for destructive actions
        if action in DESTRUCTIVE_ACTIONS and not confirmed:
            # Build a descriptive target string per action type
            if action == "move":
                target = f"{params.get('src', '?')} → {params.get('dst', '?')}"
            else:
                target = params.get("path", "?")
            return SkillResult(
                success=False,
                output=None,
                requires_confirm=True,
                confirm_prompt=f"⚠️ Destructive action `{action}` on `{target}`. Confirm to proceed.",
            )

        try:
            if action == "read_file":
                return await self._read_file(params)
            elif action == "write_file":
                return await self._write_file(params)
            elif action == "list_dir":
                return self._list_dir(params)
            elif action == "delete":
                return self._delete(params)
            elif action == "move":
                return self._move(params)
            elif action == "mkdir":
                return self._mkdir(params)
            elif action == "search_files":
                return self._search_files(params)
            elif action == "stat":
                return self._stat(params)
            else:
                return SkillResult(False, None, error=f"Unknown action: {action}")
        except PermissionError as e:
            logger.error(f"Permission denied: {e}")
            return SkillResult(False, None, error=f"Permission denied: {str(e)}")
        except FileNotFoundError as e:
            return SkillResult(False, None, error=f"File not found: {str(e)}")
        except Exception as e:
            logger.error(f"FileSystemSkill.{action} error: {e}", exc_info=True)
            return SkillResult(False, None, error=f"{type(e).__name__}: {str(e)}")

    async def _read_file(self, params: dict) -> SkillResult:
        path = params.get("path")
        if not path:
            return SkillResult(False, None, error="'path' is required.")
        async with aiofiles.open(path, "r", errors="replace") as f:
            content = await f.read()
        return SkillResult(True, {"path": path, "content": content, "size": len(content)})

    async def _write_file(self, params: dict) -> SkillResult:
        path = params.get("path")
        content = params.get("content", "")
        if not path:
            return SkillResult(False, None, error="'path' is required.")
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        async with aiofiles.open(path, "w") as f:
            await f.write(content)
        logger.info(f"Wrote file: {path} ({len(content)} chars)")
        return SkillResult(True, {"path": path, "bytes_written": len(content.encode())})

    def _list_dir(self, params: dict) -> SkillResult:
        path = params.get("path", ".")
        entries = []
        for entry in os.scandir(path):
            entries.append({
                "name": entry.name,
                "type": "dir" if entry.is_dir() else "file",
                "size": entry.stat().st_size if entry.is_file() else None,
                "path": entry.path,
            })
        return SkillResult(True, {"path": path, "entries": entries, "count": len(entries)})

    def _delete(self, params: dict) -> SkillResult:
        path = params.get("path")
        if not path:
            return SkillResult(False, None, error="'path' is required.")
        if os.path.isdir(path):
            shutil.rmtree(path)
            logger.warning(f"Deleted directory: {path}")
        else:
            os.remove(path)
            logger.warning(f"Deleted file: {path}")
        return SkillResult(True, {"deleted": path})

    def _move(self, params: dict) -> SkillResult:
        src = params.get("src")
        dst = params.get("dst")
        if not src or not dst:
            return SkillResult(False, None, error="'src' and 'dst' are required.")
        shutil.move(src, dst)
        logger.info(f"Moved {src} -> {dst}")
        return SkillResult(True, {"src": src, "dst": dst})

    def _mkdir(self, params: dict) -> SkillResult:
        path = params.get("path")
        if not path:
            return SkillResult(False, None, error="'path' is required.")
        os.makedirs(path, exist_ok=True)
        return SkillResult(True, {"created": path})

    def _search_files(self, params: dict) -> SkillResult:
        pattern = params.get("pattern", "*")
        root = params.get("path", ".")
        recursive = params.get("recursive", True)
        search_pattern = os.path.join(root, "**", pattern) if recursive else os.path.join(root, pattern)
        matches = glob.glob(search_pattern, recursive=recursive)
        return SkillResult(True, {"pattern": pattern, "matches": matches, "count": len(matches)})

    def _stat(self, params: dict) -> SkillResult:
        path = params.get("path")
        if not path:
            return SkillResult(False, None, error="'path' is required.")
        s = Path(path).stat()
        return SkillResult(True, {
            "path": path,
            "size": s.st_size,
            "is_dir": os.path.isdir(path),
            "is_file": os.path.isfile(path),
            "modified": s.st_mtime,
        })
