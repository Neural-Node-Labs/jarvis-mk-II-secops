"""
FolderReaderSkill - Reads all files in a folder and sends structure + content to the LLM.

CBD Contract:
  IN:  action ∈ {scan, analyze}, params dict
  OUT: SkillResult(success, output, error)

Actions
-------
scan     Walk a folder and return its tree structure + raw file contents.
         Use this when you only need the data (the agent processes it itself).

analyze  Walk a folder, build the full context payload, then forward it to
         the LLM with an optional instruction prompt and return the LLM reply.
         Use this when you want the LLM to reason about the codebase directly.

Design notes
------------
- Stateless: no instance-level caching. Every call re-walks the disk.
- Binary files are skipped (returned in a separate `skipped` list with reason).
- Per-file size cap (default 512 KB) prevents a single huge file from blowing
  the context window. The cap is tunable via the `max_file_bytes` param.
- Total context cap (default 200 KB of text) triggers truncation with a clear
  warning so the LLM always sees a well-formed, bounded payload.
- Respects an optional `include_extensions` / `exclude_extensions` filter list.
- Follows symlinks by default; set `follow_symlinks=false` to disable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import urllib.request
import urllib.error

from core.skill_registry import SkillRegistry, SkillResult

logger = logging.getLogger("skill.folder_reader")

# ---------------------------------------------------------------------------
# Constants / tuneable defaults
# ---------------------------------------------------------------------------
DEFAULT_MAX_FILE_BYTES   = 512 * 1024        # 512 KB per file
DEFAULT_MAX_CONTEXT_BYTES = 200 * 1024       # 200 KB total text sent to LLM
DEFAULT_LLM_URL          = "http://localhost:8000/api/llm"   # internal proxy
LLM_API_URL              = os.getenv("LLM_API_URL", DEFAULT_LLM_URL)

# Extensions we treat as plain-text (everything else is considered binary)
_TEXT_EXTENSIONS: frozenset[str] = frozenset({
    # source code
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".h", ".hpp",
    ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".scala", ".lua",
    ".r", ".m", ".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat", ".cmd",
    # markup / data
    ".html", ".htm", ".xml", ".json", ".yaml", ".yml", ".toml", ".ini",
    ".cfg", ".conf", ".env", ".properties",
    # docs
    ".md", ".rst", ".txt", ".csv", ".tsv", ".log",
    # web / style
    ".css", ".scss", ".sass", ".less", ".svg",
    # misc
    ".sql", ".graphql", ".proto", ".tf", ".hcl", ".dockerfile",
    # no extension (Makefile, Dockerfile, etc.) handled separately
})

# Directories that are almost never useful to include
_DEFAULT_EXCLUDE_DIRS: frozenset[str] = frozenset({
    ".git", ".svn", ".hg",
    "__pycache__", ".pytest_cache", ".mypy_cache",
    "node_modules", ".venv", "venv", "env",
    "dist", "build", ".next", ".nuxt",
    ".idea", ".vscode",
})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_text_file(path: Path) -> bool:
    """Return True if the file extension is in the known text set,
    OR if the file has no extension (Makefile, Dockerfile, etc.)."""
    suffix = path.suffix.lower()
    if suffix == "":
        return True   # no-extension files are usually plain text
    return suffix in _TEXT_EXTENSIONS


def _build_tree_lines(
    root: Path,
    current: Path,
    prefix: str = "",
    follow_symlinks: bool = True,
    exclude_dirs: frozenset[str] = _DEFAULT_EXCLUDE_DIRS,
) -> list[str]:
    """Recursively build a pretty-printed tree (like the `tree` CLI)."""
    lines: list[str] = []
    try:
        entries = sorted(
            current.iterdir(),
            key=lambda p: (p.is_file(), p.name.lower()),  # dirs first
        )
    except PermissionError:
        lines.append(f"{prefix}[permission denied]")
        return lines

    for i, entry in enumerate(entries):
        is_last = i == len(entries) - 1
        connector = "└── " if is_last else "├── "
        child_prefix = prefix + ("    " if is_last else "│   ")

        if entry.is_symlink() and not follow_symlinks:
            lines.append(f"{prefix}{connector}{entry.name} → [symlink skipped]")
            continue

        if entry.is_dir(follow_symlinks=follow_symlinks):
            if entry.name in exclude_dirs:
                lines.append(f"{prefix}{connector}{entry.name}/ [excluded]")
                continue
            lines.append(f"{prefix}{connector}{entry.name}/")
            lines.extend(
                _build_tree_lines(root, entry, child_prefix, follow_symlinks, exclude_dirs)
            )
        else:
            size = entry.stat().st_size if entry.exists() else 0
            lines.append(f"{prefix}{connector}{entry.name}  ({size:,} B)")

    return lines


def _collect_files(
    root: Path,
    follow_symlinks: bool,
    exclude_dirs: frozenset[str],
    include_extensions: set[str] | None,
    exclude_extensions: set[str],
    max_file_bytes: int,
) -> tuple[list[dict], list[dict]]:
    """
    Walk the directory tree and return:
      included: list of {path, rel_path, size, content}
      skipped:  list of {path, rel_path, reason}
    """
    included: list[dict] = []
    skipped:  list[dict] = []

    for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_symlinks):
        # Prune excluded dirs in-place so os.walk doesn't descend into them
        dirnames[:] = [
            d for d in sorted(dirnames)
            if d not in exclude_dirs
        ]

        for filename in sorted(filenames):
            fpath = Path(dirpath) / filename
            rel   = str(fpath.relative_to(root))
            suffix = fpath.suffix.lower()

            # Extension filter: include_extensions whitelist
            if include_extensions is not None and suffix not in include_extensions:
                skipped.append({"rel_path": rel, "reason": "extension not in include_extensions"})
                continue

            # Extension filter: exclude_extensions blacklist
            if suffix in exclude_extensions:
                skipped.append({"rel_path": rel, "reason": "extension in exclude_extensions"})
                continue

            # Binary check
            if not _is_text_file(fpath):
                skipped.append({"rel_path": rel, "reason": "binary file type"})
                continue

            # Size check
            try:
                size = fpath.stat().st_size
            except OSError as exc:
                skipped.append({"rel_path": rel, "reason": f"stat error: {exc}"})
                continue

            if size > max_file_bytes:
                skipped.append({
                    "rel_path": rel,
                    "reason": f"file too large ({size:,} B > {max_file_bytes:,} B cap)",
                })
                continue

            # Read content
            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                skipped.append({"rel_path": rel, "reason": f"read error: {exc}"})
                continue

            included.append({
                "rel_path": rel,
                "size_bytes": size,
                "content": content,
            })

    return included, skipped


def _build_llm_context(
    folder_path: str,
    tree_str: str,
    files: list[dict],
    skipped: list[dict],
    max_context_bytes: int,
    prompt: str,
) -> str:
    """
    Assemble the full text payload that will be sent to the LLM.
    Truncates file content if the total would exceed max_context_bytes,
    always appending a clear truncation notice so the LLM is aware.
    """
    header = (
        f"# Folder Analysis Request\n\n"
        f"**Root folder:** `{folder_path}`\n"
        f"**Files included:** {len(files)}  |  "
        f"**Files skipped:** {len(skipped)}\n\n"
    )

    if prompt:
        header += f"**Instruction:** {prompt}\n\n"

    tree_block = f"## Directory Structure\n\n```\n{tree_str}\n```\n\n"

    if skipped:
        skip_lines = "\n".join(f"- `{s['rel_path']}` — {s['reason']}" for s in skipped)
        skip_block = f"## Skipped Files\n\n{skip_lines}\n\n"
    else:
        skip_block = ""

    files_block_parts: list[str] = ["## File Contents\n\n"]
    total_bytes = len((header + tree_block + skip_block).encode("utf-8"))
    truncated = False

    for f in files:
        lang = f["rel_path"].rsplit(".", 1)[-1] if "." in f["rel_path"] else ""
        file_header = f"### `{f['rel_path']}`\n\n```{lang}\n"
        file_footer = "\n```\n\n"
        entry_bytes = len((file_header + f["content"] + file_footer).encode("utf-8"))

        if total_bytes + entry_bytes > max_context_bytes:
            files_block_parts.append(
                f"### `{f['rel_path']}`\n\n"
                f"[TRUNCATED — context limit of {max_context_bytes:,} B reached. "
                f"Remaining files omitted.]\n\n"
            )
            truncated = True
            break

        files_block_parts.append(file_header + f["content"] + file_footer)
        total_bytes += entry_bytes

    files_block = "".join(files_block_parts)

    footer = ""
    if truncated:
        footer = (
            "\n---\n"
            f"⚠️ **Context truncated** — only partial file contents were included "
            f"(limit: {max_context_bytes:,} B). Use `include_extensions` or "
            "`max_file_bytes` params to narrow the scope.\n"
        )

    return header + tree_block + skip_block + files_block + footer


async def _call_llm(context: str) -> str:
    """
    Forward the assembled context to the LLM via the internal API proxy.
    Returns the raw text response or raises on failure.

    The agent's own LLM routing is reused via the /api/llm internal endpoint
    so this skill stays provider-agnostic (works with Deepseek, OpenAI, etc.).
    """
    payload = json.dumps({
        "messages": [{"role": "user", "content": context}],
    }).encode("utf-8")

    req = urllib.request.Request(
        LLM_API_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    loop = asyncio.get_event_loop()

    def _do_request():
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))

    data = await loop.run_in_executor(None, _do_request)

    # Accept both {text: ...} and {content: [{text: ...}]} shapes
    if "text" in data:
        return data["text"]
    if "content" in data:
        parts = data["content"]
        if isinstance(parts, list):
            return "\n".join(p.get("text", "") for p in parts if p.get("type") == "text")
        return str(parts)
    return str(data)


# ---------------------------------------------------------------------------
# Skill class
# ---------------------------------------------------------------------------
class FolderReaderSkill:
    """
    Reads every text file in a folder tree and either returns the raw
    structure + content (scan) or sends it to the LLM for analysis (analyze).

    Actions
    -------
    scan     Return tree + file contents as structured data. No LLM call.
    analyze  Build context payload and call the LLM; return its response.
    """

    description = (
        "Given a folder path, walks the entire directory tree, reads all "
        "readable text files, and either returns the raw structure + content "
        "(scan) or forwards everything to the LLM with an optional instruction "
        "prompt for analysis, review, summarization, or any code-level task "
        "(analyze)."
    )
    actions = ["scan", "analyze"]

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------
    async def execute(self, action: str, params: dict, confirmed: bool = False) -> SkillResult:
        dispatch = {
            "scan":    self._scan,
            "analyze": self._analyze,
        }
        handler = dispatch.get(action)
        if handler is None:
            return SkillResult(
                False, None,
                error=f"Unknown action '{action}'. Valid actions: {self.actions}",
            )
        return await handler(params)

    # ------------------------------------------------------------------
    # Shared param parsing
    # ------------------------------------------------------------------
    def _parse_params(self, params: dict) -> tuple[Path, dict, str | None]:
        """
        Validate and normalise common params. Returns (root_path, options, error_msg).
        error_msg is None on success.
        """
        folder = params.get("folder") or params.get("path")
        if not folder:
            return None, {}, "'folder' (or 'path') is required."

        root = Path(folder).expanduser().resolve()
        if not root.exists():
            return None, {}, f"Folder '{folder}' does not exist."
        if not root.is_dir():
            return None, {}, f"'{folder}' is not a directory."

        # Optional extension filters
        raw_include = params.get("include_extensions")
        raw_exclude = params.get("exclude_extensions", [])
        include_ext = (
            {e.lower() if e.startswith(".") else f".{e.lower()}" for e in raw_include}
            if raw_include else None
        )
        exclude_ext = {
            e.lower() if e.startswith(".") else f".{e.lower()}" for e in raw_exclude
        }

        # Additional dirs to exclude on top of defaults
        extra_excl_dirs = set(params.get("exclude_dirs", []))
        exclude_dirs = _DEFAULT_EXCLUDE_DIRS | extra_excl_dirs

        options = {
            "max_file_bytes":    int(params.get("max_file_bytes",    DEFAULT_MAX_FILE_BYTES)),
            "max_context_bytes": int(params.get("max_context_bytes", DEFAULT_MAX_CONTEXT_BYTES)),
            "follow_symlinks":   bool(params.get("follow_symlinks",  True)),
            "include_extensions": include_ext,
            "exclude_extensions": exclude_ext,
            "exclude_dirs":       exclude_dirs,
        }
        return root, options, None

    def _walk(self, root: Path, options: dict) -> tuple[str, list[dict], list[dict]]:
        """Build tree string + collected files. Synchronous — wrap in executor for async."""
        tree_lines = _build_tree_lines(
            root, root,
            follow_symlinks=options["follow_symlinks"],
            exclude_dirs=options["exclude_dirs"],
        )
        tree_str = str(root) + "/\n" + "\n".join(tree_lines)

        files, skipped = _collect_files(
            root=root,
            follow_symlinks=options["follow_symlinks"],
            exclude_dirs=options["exclude_dirs"],
            include_extensions=options["include_extensions"],
            exclude_extensions=options["exclude_extensions"],
            max_file_bytes=options["max_file_bytes"],
        )
        return tree_str, files, skipped

    # ------------------------------------------------------------------
    # Action: scan
    # ------------------------------------------------------------------
    async def _scan(self, params: dict) -> SkillResult:
        """
        IN params:
          folder               (str,      required) — path to scan
          include_extensions   (list[str], optional) — whitelist e.g. [".py", ".js"]
          exclude_extensions   (list[str], optional) — blacklist e.g. [".lock"]
          exclude_dirs         (list[str], optional) — extra dirs to skip
          max_file_bytes       (int,       optional) — per-file cap, default 524288
          follow_symlinks      (bool,      optional) — default true

        OUT output:
          {
            "folder":        str,
            "tree":          str,          # pretty-printed directory tree
            "file_count":    int,
            "skipped_count": int,
            "files": [
              {"rel_path": str, "size_bytes": int, "content": str}, ...
            ],
            "skipped": [
              {"rel_path": str, "reason": str}, ...
            ]
          }
        """
        root, options, err = self._parse_params(params)
        if err:
            return SkillResult(False, None, error=err)

        loop = asyncio.get_event_loop()
        tree_str, files, skipped = await loop.run_in_executor(
            None, self._walk, root, options
        )

        logger.info(
            "scan: %s — %d files included, %d skipped",
            root, len(files), len(skipped),
        )
        return SkillResult(
            True,
            output={
                "folder":        str(root),
                "tree":          tree_str,
                "file_count":    len(files),
                "skipped_count": len(skipped),
                "files":         files,
                "skipped":       skipped,
            },
        )

    # ------------------------------------------------------------------
    # Action: analyze
    # ------------------------------------------------------------------
    async def _analyze(self, params: dict) -> SkillResult:
        """
        IN params:
          folder               (str,      required) — path to analyze
          prompt               (str,      optional) — instruction for the LLM
                                 e.g. "Review this codebase for security issues"
                                 Defaults to a general analysis request.
          include_extensions   (list[str], optional)
          exclude_extensions   (list[str], optional)
          exclude_dirs         (list[str], optional)
          max_file_bytes       (int,       optional) — default 524288
          max_context_bytes    (int,       optional) — total LLM context cap, default 204800
          follow_symlinks      (bool,      optional) — default true

        OUT output:
          {
            "folder":        str,
            "file_count":    int,
            "skipped_count": int,
            "skipped":       list[dict],
            "prompt":        str,
            "llm_response":  str,
            "context_bytes": int,
          }
        """
        root, options, err = self._parse_params(params)
        if err:
            return SkillResult(False, None, error=err)

        prompt: str = params.get("prompt", (
            "Analyze the following codebase. Provide a structured summary covering: "
            "1) overall architecture and purpose, 2) key components and how they interact, "
            "3) any potential issues, bugs, or improvements you notice."
        ))

        loop = asyncio.get_event_loop()
        tree_str, files, skipped = await loop.run_in_executor(
            None, self._walk, root, options
        )

        context = _build_llm_context(
            folder_path=str(root),
            tree_str=tree_str,
            files=files,
            skipped=skipped,
            max_context_bytes=options["max_context_bytes"],
            prompt=prompt,
        )
        context_bytes = len(context.encode("utf-8"))

        logger.info(
            "analyze: %s — %d files, %d skipped, context=%d B, sending to LLM",
            root, len(files), len(skipped), context_bytes,
        )

        try:
            llm_response = await _call_llm(context)
        except Exception as exc:
            return SkillResult(
                False, None,
                error=(
                    f"LLM call failed: {type(exc).__name__}: {exc}. "
                    "Use action='scan' to retrieve raw content without an LLM call."
                ),
            )

        return SkillResult(
            True,
            output={
                "folder":       str(root),
                "file_count":   len(files),
                "skipped_count": len(skipped),
                "skipped":      skipped,
                "prompt":       prompt,
                "llm_response": llm_response,
                "context_bytes": context_bytes,
            },
        )


# ---------------------------------------------------------------------------
# Registration hook
# ---------------------------------------------------------------------------
def register(registry: SkillRegistry) -> None:
    """Register FolderReaderSkill under the canonical skill name."""
    registry.register("folder_reader", FolderReaderSkill())
