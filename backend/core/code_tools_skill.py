"""
Code Tools Skill — glob / grep / read / write / edit / run_command.
Gives the agent the primitives for a real Search → Edit → Validate coding loop.
No Pydantic. Follows the same CBD contract as every other skill in this
registry (SkillResult, execute(action, params, confirmed)).

version: 1.0.0
changelog:
  1.0.0 - Initial implementation. Six actions across two logical groups:
            search   : glob, grep, read
            mutate   : write, edit
            validate : run_command
          All filesystem actions are hard-confined to JARVIS_WORKSPACE_ROOT
          (same env var core/security.py already scrubs paths against) —
          symlink-resolved, so `..`/symlink escapes are rejected rather than
          silently sandboxed-away. run_command's cwd is confined the same way.
          No shell-command allowlist is imposed here — os_execution already
          owns "unrestricted OS control"; this skill's run_command exists
          specifically for the validation step (tests/build/lint) of the
          coding loop and stays inside the workspace by default.

CBD Component Contract:
  Name:             CodeToolsSkill
  Logical Function: Agentic coding primitives (search / mutate / validate)
  IN-Schema:
    glob        → { pattern: str, root?: str, max_results?: int }
    grep        → { pattern: str, root?: str, file_glob?: str, max_results?: int,
                     case_sensitive?: bool, context_lines?: int }
    read        → { path: str, start_line?: int, end_line?: int, max_bytes?: int }
    write       → { path: str, content: str, mode?: "overwrite"|"create_only" }
    edit        → { path: str, old_str: str, new_str: str, expected_occurrences?: int }
    run_command → { command: str, cwd?: str, timeout_s?: int, env?: dict }
  OUT-Schema:   see each _execute_* docstring below
  Error-Schema: { error_code: CT_* , message: str }
  Trace Points: search_run, edit_applied, write_applied, command_run, command_timeout
  Failure Map:  every action wrapped in try/except; workspace-escape and
                missing/ambiguous edits return SkillResult.fail with an
                actionable message instead of raising
"""
import asyncio
import fnmatch
import logging
import os
import re
import time

logger = logging.getLogger("skill.code_tools")

try:
    from core.skill_registry import SkillResult
except ImportError:  # pragma: no cover - allows standalone import/testing
    from dataclasses import dataclass
    from typing import Any, Optional

    @dataclass
    class SkillResult:
        success: bool = True
        output: Any = None
        error: Optional[str] = None
        requires_confirm: bool = False
        confirm_prompt: str = ""

        def to_dict(self) -> dict:
            return {"success": self.success, "output": self.output, "error": self.error,
                    "requires_confirm": self.requires_confirm, "confirm_prompt": self.confirm_prompt}

        @staticmethod
        def ok(output=None): return SkillResult(True, output=output)

        @staticmethod
        def fail(error: str): return SkillResult(False, error=error)


# ── Configuration ───────────────────────────────────────────────────────────────
try:
    from core.settings_store import get_setting as _get_setting
except ImportError:  # pragma: no cover - standalone import/testing fallback
    def _get_setting(key: str, default=None):
        return os.getenv(key, default)


def _workspace_root() -> str:
    """
    Live-read the configured workspace root (settings.json override → env →
    default) on every call — NOT a module-level constant. A constant here
    would go stale the moment an operator edits the workspace path from the
    Settings UI without restarting the process.
    """
    return _get_setting("JARVIS_WORKSPACE_ROOT", "/app/workspace")


MAX_FILE_BYTES   = int(os.getenv("CT_MAX_FILE_BYTES",   str(1 * 1024 * 1024)))   # 1 MB read cap
MAX_OUTPUT_BYTES = int(os.getenv("CT_MAX_OUTPUT_BYTES", str(200 * 1024)))        # run_command stdout/stderr cap
DEFAULT_TIMEOUT_S = int(os.getenv("CT_DEFAULT_TIMEOUT_S", "120"))
MAX_TIMEOUT_S     = int(os.getenv("CT_MAX_TIMEOUT_S",     "900"))

# Directories skipped during glob/grep tree walks by default — noise, not code.
_DEFAULT_EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    ".mypy_cache", ".pytest_cache", ".next", ".turbo", "target", ".idea", ".vscode",
}
_BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".tar",
    ".gz", ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".mp3", ".wav", ".so",
    ".dylib", ".dll", ".exe", ".pyc", ".db", ".sqlite", ".sqlite3",
}


# ── Path safety ──────────────────────────────────────────────────────────────────

def _resolve_in_workspace(rel_or_abs: str) -> tuple:
    """
    Resolve a user/agent-supplied path against WORKSPACE_ROOT and verify it
    doesn't escape (symlink-resolved). Returns (ok, resolved_abs_path_or_err).
    Relative paths are resolved relative to the workspace root, not CWD.
    """
    try:
        root = os.path.realpath(_workspace_root())
        os.makedirs(root, exist_ok=True)
        candidate = rel_or_abs if os.path.isabs(rel_or_abs) else os.path.join(root, rel_or_abs)
        resolved = os.path.realpath(candidate)
    except Exception as exc:
        return False, f"CT_INVALID_PATH: {exc}"

    if resolved != root and not resolved.startswith(root + os.sep):
        return False, f"CT_PATH_ESCAPE: '{rel_or_abs}' resolves outside workspace root '{root}'"
    return True, resolved


def _is_probably_binary(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    if ext in _BINARY_EXTS:
        return True
    try:
        with open(path, "rb") as f:
            chunk = f.read(2048)
        return b"\x00" in chunk
    except Exception:
        return False


def _walk_workspace(root_abs: str, exclude_dirs: set):
    for dirpath, dirnames, filenames in os.walk(root_abs):
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs and not d.startswith(".git")]
        for fn in filenames:
            yield os.path.join(dirpath, fn)


# ── Action: glob ──────────────────────────────────────────────────────────────

def _execute_glob(params: dict) -> "SkillResult":
    """
    IN:  { pattern: str, root?: str, max_results?: int }
    OUT: { matches: [relpath...], count, truncated }
    `pattern` is a shell-style glob (fnmatch), matched against each file's path
    relative to `root` (default: workspace root). e.g. "**/*.py", "src/*.ts".
    """
    pattern = params.get("pattern", "").strip()
    if not pattern:
        return SkillResult.fail("CT_NO_PATTERN: 'pattern' is required (e.g. '**/*.py')")

    ok, root_abs = _resolve_in_workspace(params.get("root", "."))
    if not ok:
        return SkillResult.fail(root_abs)
    if not os.path.isdir(root_abs):
        return SkillResult.fail(f"CT_NOT_A_DIR: '{root_abs}' is not a directory")

    max_results = int(params.get("max_results", 200))
    # fnmatch doesn't understand "**" as "any depth" out of the box — normalise
    # it to match both the flat and nested case so "**/*.py" behaves as expected.
    norm_pattern = pattern.replace("**/", "*/").lstrip("./")

    matches: list = []
    truncated = False
    for full_path in _walk_workspace(root_abs, _DEFAULT_EXCLUDE_DIRS):
        rel = os.path.relpath(full_path, root_abs)
        rel_posix = rel.replace(os.sep, "/")
        if fnmatch.fnmatch(rel_posix, pattern) or fnmatch.fnmatch(rel_posix, norm_pattern) \
           or fnmatch.fnmatch(os.path.basename(rel_posix), pattern):
            matches.append(rel_posix)
            if len(matches) >= max_results:
                truncated = True
                break

    matches.sort()
    logger.info("[search_run] action=glob pattern=%s matches=%d truncated=%s", pattern, len(matches), truncated)
    return SkillResult.ok({"matches": matches, "count": len(matches), "truncated": truncated})


# ── Action: grep ──────────────────────────────────────────────────────────────

def _execute_grep(params: dict) -> "SkillResult":
    """
    IN:  { pattern: str (regex), root?: str, file_glob?: str="*", max_results?: int=200,
           case_sensitive?: bool=True, context_lines?: int=0 }
    OUT: { matches: [{file, line_no, line, context_before?, context_after?}], count, truncated }
    """
    pattern = params.get("pattern", "")
    if not pattern:
        return SkillResult.fail("CT_NO_PATTERN: 'pattern' (regex) is required")

    ok, root_abs = _resolve_in_workspace(params.get("root", "."))
    if not ok:
        return SkillResult.fail(root_abs)

    file_glob      = params.get("file_glob", "*")
    max_results    = int(params.get("max_results", 200))
    case_sensitive = bool(params.get("case_sensitive", True))
    context_lines  = max(0, int(params.get("context_lines", 0)))

    try:
        flags = 0 if case_sensitive else re.IGNORECASE
        regex = re.compile(pattern, flags)
    except re.error as exc:
        return SkillResult.fail(f"CT_BAD_REGEX: {exc}")

    matches: list = []
    truncated = False

    for full_path in _walk_workspace(root_abs, _DEFAULT_EXCLUDE_DIRS):
        rel_posix = os.path.relpath(full_path, root_abs).replace(os.sep, "/")
        if not fnmatch.fnmatch(os.path.basename(rel_posix), file_glob):
            continue
        try:
            if os.path.getsize(full_path) > MAX_FILE_BYTES or _is_probably_binary(full_path):
                continue
        except OSError:
            continue

        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.read().splitlines()
        except Exception:
            continue

        for i, line in enumerate(lines):
            if regex.search(line):
                entry = {"file": rel_posix, "line_no": i + 1, "line": line}
                if context_lines:
                    entry["context_before"] = lines[max(0, i - context_lines):i]
                    entry["context_after"]  = lines[i + 1:i + 1 + context_lines]
                matches.append(entry)
                if len(matches) >= max_results:
                    truncated = True
                    break
        if truncated:
            break

    logger.info("[search_run] action=grep pattern=%s matches=%d truncated=%s", pattern, len(matches), truncated)
    return SkillResult.ok({"matches": matches, "count": len(matches), "truncated": truncated})


# ── Action: read ──────────────────────────────────────────────────────────────

def _execute_read(params: dict) -> "SkillResult":
    """
    IN:  { path: str, start_line?: int, end_line?: int, max_bytes?: int }
    OUT: { path, content (numbered), total_lines, truncated }
    Content is returned with "N\tline" prefixes (1-indexed) — mirrors the
    convention the model already sees from other file-viewing tools in this
    stack, which keeps str_replace-style edits addressable by line number.
    """
    path = params.get("path", "")
    if not path:
        return SkillResult.fail("CT_NO_PATH: 'path' is required")

    ok, abs_path = _resolve_in_workspace(path)
    if not ok:
        return SkillResult.fail(abs_path)
    if not os.path.isfile(abs_path):
        return SkillResult.fail(f"CT_NOT_FOUND: '{path}' is not a file")

    max_bytes = int(params.get("max_bytes", MAX_FILE_BYTES))
    try:
        size = os.path.getsize(abs_path)
        if _is_probably_binary(abs_path):
            return SkillResult.fail(f"CT_BINARY_FILE: '{path}' looks binary — not readable as text")
        with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read(max_bytes + 1)
        truncated_bytes = len(raw) > max_bytes
        if truncated_bytes:
            raw = raw[:max_bytes]
    except Exception as exc:
        return SkillResult.fail(f"CT_READ_ERROR: {exc}")

    lines = raw.splitlines()
    total_lines = len(lines)

    start = int(params["start_line"]) if params.get("start_line") else 1
    end   = int(params["end_line"])   if params.get("end_line")   else total_lines
    start = max(1, start)
    end   = min(total_lines, end)

    numbered = "\n".join(f"{n}\t{lines[n - 1]}" for n in range(start, end + 1)) if total_lines else ""

    return SkillResult.ok({
        "path": path, "content": numbered, "total_lines": total_lines,
        "shown_range": [start, end] if total_lines else [0, 0],
        "truncated": truncated_bytes or (end < total_lines), "size_bytes": size,
    })


# ── Action: write ──────────────────────────────────────────────────────────────

def _execute_write(params: dict) -> "SkillResult":
    """
    IN:  { path: str, content: str, mode?: "overwrite"|"create_only" (default "overwrite") }
    OUT: { path, bytes_written, created: bool }
    Creates parent directories as needed. Use `edit` instead of `write` for
    targeted changes to existing files — `write` replaces the whole file.
    """
    path = params.get("path", "")
    content = params.get("content", "")
    mode = params.get("mode", "overwrite")
    if not path:
        return SkillResult.fail("CT_NO_PATH: 'path' is required")
    if mode not in ("overwrite", "create_only"):
        return SkillResult.fail("CT_BAD_MODE: mode must be 'overwrite' or 'create_only'")

    ok, abs_path = _resolve_in_workspace(path)
    if not ok:
        return SkillResult.fail(abs_path)

    existed = os.path.isfile(abs_path)
    if existed and mode == "create_only":
        return SkillResult.fail(f"CT_ALREADY_EXISTS: '{path}' exists — use mode='overwrite' or the 'edit' action")

    try:
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as exc:
        return SkillResult.fail(f"CT_WRITE_ERROR: {exc}")

    logger.info("[write_applied] path=%s bytes=%d created=%s", path, len(content), not existed)
    return SkillResult.ok({"path": path, "bytes_written": len(content.encode("utf-8")), "created": not existed})


# ── Action: edit ──────────────────────────────────────────────────────────────

def _execute_edit(params: dict) -> "SkillResult":
    """
    IN:  { path: str, old_str: str, new_str: str, expected_occurrences?: int=1 }
    OUT: { path, occurrences_replaced }
    Exact-match find/replace, like a surgical patch. `old_str` must appear
    exactly `expected_occurrences` times (default 1) or the edit is rejected
    with an actionable error — this is deliberate: a silent multi-replace or
    a silent no-op is worse than a loud failure the agent can react to.
    """
    path    = params.get("path", "")
    old_str = params.get("old_str", "")
    new_str = params.get("new_str", "")
    expected = int(params.get("expected_occurrences", 1))

    if not path:
        return SkillResult.fail("CT_NO_PATH: 'path' is required")
    if not old_str:
        return SkillResult.fail("CT_NO_OLD_STR: 'old_str' is required (use 'write' to create new files)")

    ok, abs_path = _resolve_in_workspace(path)
    if not ok:
        return SkillResult.fail(abs_path)
    if not os.path.isfile(abs_path):
        return SkillResult.fail(f"CT_NOT_FOUND: '{path}' does not exist — use 'write' to create it")

    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            original = f.read()
    except Exception as exc:
        return SkillResult.fail(f"CT_READ_ERROR: {exc}")

    count = original.count(old_str)
    if count == 0:
        return SkillResult.fail(
            "CT_NO_MATCH: old_str not found in file. Re-read the file — whitespace, "
            "indentation, or a prior edit may have changed it since you last saw it."
        )
    if count != expected:
        return SkillResult.fail(
            f"CT_AMBIGUOUS_MATCH: old_str appears {count} times, expected {expected}. "
            f"Include more surrounding context to make old_str unique, or pass "
            f"expected_occurrences={count} if replacing all of them is intentional."
        )

    updated = original.replace(old_str, new_str, count if expected != 1 else 1) if expected != 1 else original.replace(old_str, new_str, 1)
    # For expected>1 with count==expected, replace all occurrences intentionally.
    if expected == count and expected != 1:
        updated = original.replace(old_str, new_str)

    try:
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(updated)
    except Exception as exc:
        return SkillResult.fail(f"CT_WRITE_ERROR: {exc}")

    logger.info("[edit_applied] path=%s occurrences=%d", path, count)
    return SkillResult.ok({"path": path, "occurrences_replaced": count})


# ── Action: run_command ──────────────────────────────────────────────────────

async def _execute_run_command(params: dict) -> "SkillResult":
    """
    IN:  { command: str, cwd?: str, timeout_s?: int, env?: dict }
    OUT: { command, cwd, exit_code, stdout, stderr, duration_ms, timed_out }
    This is the validation primitive: run the test suite / linter / type
    checker / repro steps and read exit_code back. cwd defaults to (and is
    confined to) the workspace root. Runs via the shell (so pipes/&&/env
    work) — same trust model as this stack's existing os_execution skill.
    """
    command = params.get("command", "").strip()
    if not command:
        return SkillResult.fail("CT_NO_COMMAND: 'command' is required")

    ok, cwd_abs = _resolve_in_workspace(params.get("cwd", "."))
    if not ok:
        return SkillResult.fail(ok if isinstance(ok, str) else cwd_abs)
    if not os.path.isdir(cwd_abs):
        return SkillResult.fail(f"CT_NOT_A_DIR: cwd '{params.get('cwd', '.')}' is not a directory")

    timeout_s = min(int(params.get("timeout_s", DEFAULT_TIMEOUT_S)), MAX_TIMEOUT_S)
    env = {**os.environ, **(params.get("env") or {})}

    started = time.monotonic()
    proc = None
    try:
        proc = await asyncio.create_subprocess_shell(
            command, cwd=cwd_abs, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
            timed_out = False
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            stdout_b, stderr_b = b"", b""
            timed_out = True
    except Exception as exc:
        return SkillResult.fail(f"CT_EXEC_ERROR: {type(exc).__name__}: {exc}")

    duration_ms = int((time.monotonic() - started) * 1000)
    stdout = stdout_b.decode("utf-8", errors="replace")[:MAX_OUTPUT_BYTES]
    stderr = stderr_b.decode("utf-8", errors="replace")[:MAX_OUTPUT_BYTES]
    exit_code = proc.returncode if proc is not None and not timed_out else None

    if timed_out:
        logger.warning("[command_timeout] cmd=%.80s timeout_s=%d", command, timeout_s)
        return SkillResult.fail(
            f"CT_TIMEOUT: command exceeded {timeout_s}s and was killed. "
            f"Narrow the scope (fewer tests / smaller target) or raise timeout_s."
        )

    logger.info("[command_run] cmd=%.80s exit_code=%s duration_ms=%d", command, exit_code, duration_ms)
    return SkillResult.ok({
        "command": command, "cwd": os.path.relpath(cwd_abs, os.path.realpath(_workspace_root())) or ".",
        "exit_code": exit_code, "stdout": stdout, "stderr": stderr,
        "duration_ms": duration_ms, "timed_out": False,
    })


# ── Public skill interface ─────────────────────────────────────────────────────

class CodeToolsSkill:
    """CBD-compliant skill class registered with SkillRegistry."""

    SKILL_NAME = "code_tools"

    ACTIONS = {
        "glob":        "Find files by name/path pattern (agentic file search)",
        "grep":        "Search file contents by regex across the tree (agentic content search)",
        "read":        "Read a file (optionally a line range), numbered for precise editing",
        "write":       "Create or overwrite a file's full contents",
        "edit":        "Exact find-and-replace patch on an existing file",
        "run_command":  "Run a shell command (tests/build/lint/repro) and read exit_code back — the validation step",
    }

    async def execute(self, action: str, params: dict, confirmed: bool = False):
        params = params or {}
        try:
            if action == "glob":
                return _execute_glob(params)
            elif action == "grep":
                return _execute_grep(params)
            elif action == "read":
                return _execute_read(params)
            elif action == "write":
                return _execute_write(params)
            elif action == "edit":
                return _execute_edit(params)
            elif action == "run_command":
                return await _execute_run_command(params)
            else:
                return SkillResult.fail(
                    f"CT_UNKNOWN_ACTION: '{action}'. Valid actions: {', '.join(self.ACTIONS)}"
                )
        except Exception as exc:
            logger.error("[code_tools.execute_error] action=%s err=%s", action, exc)
            return SkillResult.fail(f"CT_INTERNAL_ERROR: {type(exc).__name__}: {exc}")

    def describe(self) -> dict:
        return {
            "name": self.SKILL_NAME,
            "description": "Agentic coding primitives: search (glob/grep/read), "
                            "mutate (write/edit), validate (run_command).",
            "actions": self.ACTIONS,
            "config": {
                "workspace_root_env": "JARVIS_WORKSPACE_ROOT (default /app/workspace)",
                "max_file_bytes_env": "CT_MAX_FILE_BYTES (default 1MB)",
                "default_timeout_env": "CT_DEFAULT_TIMEOUT_S (default 120s)",
            },
        }
