"""
Folder Reader Skill — Read entire folder trees with content summarisation.
Useful for feeding project structure + key files to the agent at once.

version: 1.0.0
changelog:
  1.0.0 - 2026-06-10 - Initial. Actions: read_tree, summarise.
"""
import os
import fnmatch
import logging
from core.skill_registry import SkillResult

logger = logging.getLogger("skill.folder_reader")

SKIP_DIRS  = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build", ".pytest_cache"}
SKIP_EXTS  = {".pyc", ".pyo", ".so", ".dll", ".exe", ".bin", ".jpg", ".png", ".gif", ".svg",
              ".zip", ".tar", ".gz", ".lock"}
TEXT_EXTS  = {".py", ".js", ".ts", ".jsx", ".tsx", ".md", ".txt", ".json", ".yaml", ".yml",
              ".toml", ".cfg", ".ini", ".sh", ".bash", ".env", ".html", ".css", ".sql", ".go",
              ".rs", ".java", ".c", ".cpp", ".h", ".xml"}
MAX_FILE_BYTES = 32_000
MAX_TOTAL_BYTES = 512_000


class FolderReaderSkill:

    async def execute(self, action: str, params: dict, confirmed: bool = False) -> SkillResult:
        dispatch = {
            "read_tree":  self._read_tree,
            "summarise":  self._summarise,
            "summarize":  self._summarise,
        }
        fn = dispatch.get(action)
        if fn is None:
            return SkillResult.fail(f"FOLDER_UNKNOWN_ACTION: '{action}'. Valid: {', '.join(dispatch)}")
        return await fn(params, confirmed)

    async def _read_tree(self, params: dict, confirmed: bool) -> SkillResult:
        root          = params.get("path", ".")
        max_depth     = int(params.get("max_depth", 4))
        include_files = params.get("include_content", True)
        pattern       = params.get("pattern", "*")

        if not os.path.isdir(root):
            return SkillResult.fail(f"FOLDER_NOT_DIR: '{root}'")

        tree      = []
        files_out = []
        total_bytes = 0

        for dirpath, dirs, files in os.walk(root):
            # Depth gate
            depth = dirpath.replace(root, "").count(os.sep)
            if depth > max_depth:
                dirs.clear()
                continue

            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)

            rel_dir = os.path.relpath(dirpath, root)
            indent  = "  " * depth
            tree.append(f"{indent}📁 {os.path.basename(dirpath) or root}/")

            for fname in sorted(files):
                if not fnmatch.fnmatch(fname, pattern):
                    continue
                _, ext = os.path.splitext(fname)
                if ext in SKIP_EXTS:
                    continue

                fpath = os.path.join(dirpath, fname)
                fsize = os.path.getsize(fpath)
                tree.append(f"{indent}  📄 {fname} ({fsize} bytes)")

                if include_files and ext in TEXT_EXTS and total_bytes < MAX_TOTAL_BYTES:
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                            content = f.read(MAX_FILE_BYTES)
                        truncated = fsize > MAX_FILE_BYTES
                        files_out.append({
                            "path":      os.path.join(rel_dir, fname),
                            "content":   content,
                            "size":      fsize,
                            "truncated": truncated,
                        })
                        total_bytes += len(content)
                    except Exception:
                        pass

        logger.info("[folder_reader.read_tree] root=%s files=%d total_bytes=%d",
                    root, len(files_out), total_bytes)
        return SkillResult.ok({
            "root":        root,
            "tree":        "\n".join(tree),
            "files":       files_out,
            "file_count":  len(files_out),
            "total_bytes": total_bytes,
        })

    async def _summarise(self, params: dict, confirmed: bool) -> SkillResult:
        """Return just the directory tree without file contents."""
        params["include_content"] = False
        return await self._read_tree(params, confirmed)
