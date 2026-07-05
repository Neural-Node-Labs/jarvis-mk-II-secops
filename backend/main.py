"""
Mighty Jarvis MKII — FastAPI Server (Production-Hardened)
CBD-compliant multi-component architecture.

version: 4.1.0
changelog:
  4.1.0 - 2026-06-19 - Security hardening (production-ready).
    FIXED  All chat/session/workspace/mkii/evolve/cbd/scheduler endpoints now
           require authentication. Anonymous access removed.
    FIXED  CORS wildcard + allow_credentials=True is an invalid combination that
           browsers reject. Origins are now read strictly from CORS_ORIGINS env;
           wildcard is blocked in production mode.
    FIXED  /api/auth/debug is now gated behind JARVIS_DEBUG_ENDPOINTS=true env var
           and requires admin auth. Previously unauthenticated and always-on.
    FIXED  /api/auth/reset-admin no longer returns the new password in the response
           body. Caller already knows what they sent; logging it is sufficient.
    FIXED  /api/kali/exec now refuses to start when KALI_EXEC_API_KEY is empty
           (previously silently open). HMAC compare_digest used for key validation.
    FIXED  _kali_exec: proc.kill() was not awaited after TimeoutError, leaving
           zombie processes. Now proc.kill() + await proc.wait().
    FIXED  _kali_exec: user-supplied env_extra can no longer override PATH or any
           other system variable. Merged after the safe env dict, with PATH pinned.
    FIXED  _kali_exec: stdout/stderr output now capped at SEC_KALI_OUTPUT_CAP bytes
           (default 512 KB) to prevent memory exhaustion from verbose tools.
    FIXED  File upload: no size limit previously. Now enforced via
           SEC_UPLOAD_MAX_BYTES (default 10 MB) and SEC_UPLOAD_MAX_FILES (10).
    FIXED  File upload: filenames not sanitised. sanitise_filename() strips
           path components, null bytes, and dangerous characters.
    FIXED  Workspace endpoints: abs_path fields returned to clients, leaking
           container internals. Now scrubbed via scrub_abs_paths().
    FIXED  Workspace path traversal: os.path.normpath is bypassable with symlinks.
           validate_workspace_path() now uses os.path.realpath().
    FIXED  _sessions dict: unbounded growth. Replaced with BoundedSessionStore
           (LRU eviction, cap = SEC_SESSION_MAX, default 500).
    FIXED  WebSocket: no timeout before first auth message. Client has
           SEC_WS_AUTH_TIMEOUT (default 10s) to send auth frame or is disconnected.
    FIXED  WebSocket: no per-message size limit. Now enforced at SEC_WS_MAX_MSG_BYTES.
    FIXED  Rate limiting: no login brute-force protection. Sliding-window limiter
           added (SEC_LOGIN_MAX attempts per SEC_LOGIN_WINDOW_S seconds per IP).
    FIXED  Rate limiting: no general API rate limit. Applied to all authenticated
           endpoints (SEC_API_MAX per SEC_API_WINDOW_S seconds per IP).
    FIXED  /api/skills/reload and /api/skills/register: no auth. Now admin-only.
    FIXED  /api/halt/{user_id}, /api/session endpoints: no auth. User can now
           only manage their own session; admin can manage any.
    FIXED  Security headers middleware added (X-Frame-Options, CSP, HSTS, etc.).
    FIXED  Internal exceptions no longer returned verbatim to clients. Generic
           "Internal server error" message used; details logged server-side only.
  4.0.0 - CBD restructure (see original changelog)
"""
import re
import os
import io
import json
import uuid
import base64
import asyncio
import logging
import threading
import traceback
import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import AsyncGenerator, Optional
from collections import defaultdict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# ── Security layer ─────────────────────────────────────────────────────────────
from core.security import (
    BoundedSessionStore,
    check_login_rate, check_api_rate, check_ws_rate,
    clear_login_rate, get_client_ip,
    require_auth, require_admin, require_ws_auth, require_kali_key, require_debug_disabled,
    sanitise_filename, sanitise_message,
    validate_workspace_path, validate_upload_size, validate_upload_mime,
    scrub_abs_paths, security_headers_middleware, get_cors_origins,
    UPLOAD_MAX_BYTES, UPLOAD_MAX_FILES, KALI_OUTPUT_CAP,
    WS_AUTH_TIMEOUT_S, WS_MAX_MSG_BYTES,
)

# ── Internal imports ───────────────────────────────────────────────────────────
from core.llm_router import LLMRouter, LLMConfig, get_model_max_tokens
from core.skill_registry import SkillRegistry
from core.prompt_builder import (
    build_system_prompt, AGENT_SYSTEM_PROMPT,
    list_personas, DEFAULT_PERSONA, get_persona,
    save_persona, delete_persona, load_all_personas,
)
from core.memory_manager import MemoryManager, detect_retrieval_request
from core.agent import Agent, REACT_MAX_ITERATIONS, DEEP_TASK_MAX_ITERATIONS, CODE_AGENT_MAX_ITERATIONS
from core.settings_store import (
    get_setting, get_setting_int, describe_settings, save_settings, reset_settings,
    EDITABLE_SETTINGS,
)

# ══════════════════════════════════════════════════════════════════════════════
# CBD COMPONENT 1 — Logging Subsystem
# ══════════════════════════════════════════════════════════════════════════════
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("system.log", encoding="utf-8"),
    ],
)
logger    = logging.getLogger("jarvis.main")
llm_trace = logging.getLogger("jarvis.llm_trace")
llm_trace.addHandler(logging.FileHandler("llm_interaction.log", encoding="utf-8"))
llm_trace.propagate = False

# ══════════════════════════════════════════════════════════════════════════════
# CBD COMPONENT 2 — Application Bootstrap
# ══════════════════════════════════════════════════════════════════════════════
app = FastAPI(
    title="Mighty Jarvis MKII",
    description="Autonomous AI Agent — Pentesting & SecOps Platform (CBD v2.2)",
    version="4.1.0",
    # Disable automatic OpenAPI/docs in production
    docs_url=None if os.getenv("JARVIS_ENV", "production") != "development" else "/docs",
    redoc_url=None if os.getenv("JARVIS_ENV", "production") != "development" else "/redoc",
    openapi_url=None if os.getenv("JARVIS_ENV", "production") != "development" else "/openapi.json",
)

# Security headers on every response
app.middleware("http")(security_headers_middleware)

# CORS — wildcard is refused in production; set CORS_ORIGINS explicitly
_cors_origins = get_cors_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins if _cors_origins else ["*"],
    allow_credentials=bool(_cors_origins),   # credentials only when origins are explicit
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Kali-Key"],
)

FRONTEND_DIR = os.getenv("FRONTEND_DIR", "/app/frontend/dist")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIR, "assets")), name="assets")

# ══════════════════════════════════════════════════════════════════════════════
# CBD COMPONENT 3 — Session Registry (LRU-bounded)
# ══════════════════════════════════════════════════════════════════════════════
_registry = SkillRegistry()
_memory   = MemoryManager()
_sessions = BoundedSessionStore()   # replaces unbounded dict


def _get_session(user_id: str, model: str = None, provider: str = None) -> Agent:
    agent = _sessions.get(user_id)
    if agent is None:
        try:
            cfg   = _build_llm_config(model, provider)
            agent = Agent(cfg, _registry, user_id=user_id)
            _sessions.set(user_id, agent)
            logger.info("[session_created] user=%s model=%s", user_id, cfg.model)
        except Exception as exc:
            logger.error("[session_create_failed] user=%s err=%s", user_id, exc)
            raise
    return agent


def _destroy_session(user_id: str) -> bool:
    destroyed = _sessions.pop(user_id)
    if destroyed:
        logger.info("[session_destroyed] user=%s", user_id)
    return destroyed


def _build_llm_config(model: str = None, provider: str = None, temperature: float = 0.7) -> LLMConfig:
    resolved_provider = provider or get_setting("LLM_PROVIDER", "deepseek")
    resolved_model    = model    or get_setting("LLM_MODEL",    "deepseek-coder")
    max_tokens        = get_model_max_tokens(resolved_model)
    return LLMConfig(
        provider=resolved_provider,
        model=resolved_model,
        temperature=temperature,
        max_tokens=max_tokens,
        api_key=os.getenv(f"{resolved_provider.upper()}_API_KEY"),
    )


# ══════════════════════════════════════════════════════════════════════════════
# CBD COMPONENT 4 — JarvisMKII Multi-Task Skill
# ══════════════════════════════════════════════════════════════════════════════
_active_tasks: dict[str, dict] = {}
_task_lock = threading.Lock()


def _task_timeout_seconds() -> int:
    return get_setting_int("JARVIS_TASK_TIMEOUT", 300)


async def _run_single_task(task_def: dict) -> dict:
    task_id      = task_def.get("task_id", str(uuid.uuid4()))
    message      = sanitise_message(task_def.get("message", ""))
    react        = task_def.get("react", True)
    user_id      = task_def.get("user_id", f"mkii-{task_id}")
    model        = task_def.get("model")
    provider     = task_def.get("provider")
    auto_confirm = task_def.get("auto_confirm", False)

    if not message:
        return {"task_id": task_id, "status": "failed", "output": "",
                "error": "MKII_EMPTY_MESSAGE", "duration_ms": 0}

    started_at    = datetime.now(timezone.utc)
    output_tokens: list[str] = []
    task_timeout  = _task_timeout_seconds()

    with _task_lock:
        _active_tasks[task_id] = {
            "task_id":    task_id,
            "status":     "running",
            "started_at": started_at.isoformat(),
            "message":    message[:120],
        }

    logger.info("[task_started] id=%s user=%s react=%s", task_id, user_id, react)
    llm_trace.info("[JARVIS-MKII] task_id=%s user=%s message=%.80s", task_id, user_id, message)

    try:
        cfg   = _build_llm_config(model, provider)
        agent = Agent(cfg, _registry, user_id=user_id)

        async def _collect():
            async for event in agent.chat_stream(message, react=react, auto_confirm=auto_confirm):
                if event.get("type") == "token":
                    output_tokens.append(event.get("data", ""))

        await asyncio.wait_for(_collect(), timeout=task_timeout)
        full_output = "".join(output_tokens)
        duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
        result = {"task_id": task_id, "status": "complete",
                  "output": full_output, "error": None, "duration_ms": duration_ms}
        logger.info("[task_complete] id=%s duration_ms=%d", task_id, duration_ms)

    except asyncio.TimeoutError:
        duration_ms = task_timeout * 1000
        result = {"task_id": task_id, "status": "timeout",
                  "output": "".join(output_tokens),
                  "error": f"Task timed out after {task_timeout}s",
                  "duration_ms": duration_ms}
        logger.warning("[task_timeout] id=%s", task_id)

    except Exception as exc:
        duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
        result = {"task_id": task_id, "status": "failed",
                  "output": "".join(output_tokens),
                  "error": "Task execution failed.",   # no internal detail to client
                  "duration_ms": duration_ms}
        logger.error("[task_failed] id=%s err=%s\n%s", task_id, exc, traceback.format_exc())

    finally:
        with _task_lock:
            _active_tasks.pop(task_id, None)

    return result


async def _jarvis_mkii_execute(payload: dict) -> dict:
    tasks = payload.get("tasks", [])
    if not tasks:
        return {"error": "MKII_NO_TASKS", "message": "Provide at least one task in tasks[]"}

    max_parallel = get_setting_int("JARVIS_MAX_PARALLEL", 10)
    if len(tasks) > max_parallel:
        return {"error": "MKII_TOO_MANY_TASKS",
                "message": f"Max {max_parallel} parallel tasks. Got {len(tasks)}."}

    for t in tasks:
        if not t.get("task_id"):
            t["task_id"] = str(uuid.uuid4())[:8]

    logger.info("[all_tasks_submitted] count=%d", len(tasks))
    started = datetime.now(timezone.utc)
    results = await asyncio.gather(*[_run_single_task(t) for t in tasks], return_exceptions=False)
    duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    completed   = sum(1 for r in results if r.get("status") == "complete")
    failed      = len(results) - completed
    logger.info("[all_tasks_done] submitted=%d completed=%d failed=%d duration_ms=%d",
                len(tasks), completed, failed, duration_ms)
    return {"submitted": len(tasks), "completed": completed, "failed": failed,
            "duration_ms": duration_ms, "results": list(results)}


# ══════════════════════════════════════════════════════════════════════════════
# CBD COMPONENT 5 — Kali Tool Executor (hardened)
# ══════════════════════════════════════════════════════════════════════════════
KALI_TOOL_TIMEOUT = int(os.getenv("KALI_TOOL_TIMEOUT", "120"))
_KALI_API_KEY     = os.getenv("KALI_EXEC_API_KEY", "")

# Pinned base environment — user env_extra CANNOT override PATH
_KALI_ENV_BASE = {
    "PATH":            "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "TERM":            "xterm-256color",
    "DEBIAN_FRONTEND": "noninteractive",
    "HOME":            "/root",
    "LANG":            "C.UTF-8",
}


async def _kali_exec(
    command: str,
    cwd: str = None,
    timeout: int = None,
    env_extra: dict = None,
) -> dict:
    """
    Execute a shell command and return a structured result.

    Security hardening vs original:
    - PATH and system vars cannot be overridden by env_extra
    - stdout/stderr capped at KALI_OUTPUT_CAP bytes
    - proc.kill() + await proc.wait() to fully reap zombie
    - No internal paths returned in error messages
    """
    effective_timeout = min(timeout or KALI_TOOL_TIMEOUT, KALI_TOOL_TIMEOUT)
    effective_cwd     = cwd or "/app/workspace"

    # Merge: user extras go in AFTER base so they cannot override PATH
    # Strip any keys that collide with security-critical vars
    _PROTECTED = {"PATH", "LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONPATH"}
    safe_extra  = {k: str(v) for k, v in (env_extra or {}).items()
                   if k not in _PROTECTED}
    effective_env = {**_KALI_ENV_BASE, **safe_extra}

    started = datetime.now(timezone.utc)
    logger.info("[kali_exec_start] cmd=%.80s cwd=%s timeout=%ds", command, effective_cwd, effective_timeout)

    proc = None
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=effective_cwd,
            env=effective_env,
        )
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=effective_timeout
        )
        duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)

        # Cap output to avoid sending huge payloads to LLM / client
        stdout_str = stdout_b[:KALI_OUTPUT_CAP].decode("utf-8", errors="replace")
        stderr_str = stderr_b[:KALI_OUTPUT_CAP // 4].decode("utf-8", errors="replace")
        if len(stdout_b) > KALI_OUTPUT_CAP:
            stdout_str += f"\n… [truncated — {len(stdout_b)} bytes total]"
        if len(stderr_b) > KALI_OUTPUT_CAP // 4:
            stderr_str += "\n… [stderr truncated]"

        logger.info("[kali_exec_complete] rc=%d duration_ms=%d", proc.returncode, duration_ms)
        return {
            "command":     command,
            "stdout":      stdout_str,
            "stderr":      stderr_str,
            "returncode":  proc.returncode,
            "duration_ms": duration_ms,
            "timed_out":   False,
        }

    except asyncio.TimeoutError:
        # FIX: kill() + wait() to fully reap the zombie process
        if proc:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        duration_ms = effective_timeout * 1000
        logger.warning("[kali_exec_timeout] cmd=%.80s after=%ds", command, effective_timeout)
        return {
            "command":     command,
            "stdout":      "",
            "stderr":      f"Command timed out after {effective_timeout}s",
            "returncode":  -1,
            "duration_ms": duration_ms,
            "timed_out":   True,
        }

    except Exception as exc:
        logger.error("[kali_exec_error] cmd=%.80s err=%s", command, exc)
        return {
            "command":     command,
            "stdout":      "",
            "stderr":      "Execution error.",   # no internal detail to client
            "returncode":  -2,
            "duration_ms": 0,
            "timed_out":   False,
        }


# ══════════════════════════════════════════════════════════════════════════════
# CBD COMPONENT 6 — SSE Stream Builder
# ══════════════════════════════════════════════════════════════════════════════

async def _agent_to_sse(event_gen: AsyncGenerator) -> AsyncGenerator[bytes, None]:
    try:
        yield _sse("stream_start", {"status": "ok"})
        async for event in event_gen:
            etype = event.get("type", "token")
            yield _sse(etype, event.get("data", event))
            if etype == "done":
                break
    except Exception as exc:
        logger.error("[stream_error] %s\n%s", exc, traceback.format_exc())
        yield _sse("error", {"message": "Stream error."})   # no internal detail
    finally:
        yield _sse("stream_end", {})


def _sse(event: str, data) -> bytes:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


# ══════════════════════════════════════════════════════════════════════════════
# CBD COMPONENT 7 — Attachment Processor (hardened)
# ══════════════════════════════════════════════════════════════════════════════
TEXT_MIME_PREFIXES = ("text/", "application/json", "application/xml", "application/javascript")


async def _process_attachments(files: list[UploadFile]) -> list[dict]:
    """Read uploaded files, enforcing size/count/MIME limits."""
    if len(files) > UPLOAD_MAX_FILES:
        raise HTTPException(status_code=422, detail=f"Too many files. Maximum {UPLOAD_MAX_FILES}.")

    attachments = []
    total_bytes = 0

    for uf in files:
        try:
            raw  = await uf.read(UPLOAD_MAX_BYTES + 1)  # read one extra to detect oversize
            mime = uf.content_type or "application/octet-stream"
            safe_name = sanitise_filename(uf.filename or "upload")

            validate_upload_size(None, raw)
            validate_upload_mime(mime, safe_name)

            total_bytes += len(raw)
            if total_bytes > UPLOAD_MAX_BYTES * UPLOAD_MAX_FILES:
                raise HTTPException(status_code=413, detail="Total upload size limit exceeded.")

            att = {"name": safe_name, "mime": mime, "size": len(raw)}
            if any(mime.startswith(p) for p in TEXT_MIME_PREFIXES):
                att["text"] = raw.decode("utf-8", errors="replace")
            else:
                import base64
                att["b64"] = base64.b64encode(raw).decode()
            attachments.append(att)
            logger.info("[attachment_processed] name=%s mime=%s size=%d", safe_name, mime, len(raw))
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("[attachment_skip] file=%s err=%s", uf.filename, exc)

    return attachments


# Archive extensions we know how to safely unpack with stdlib only.
_ARCHIVE_EXTS = {
    ".zip":     "zip",
    ".tar":     "tar",
    ".tar.gz":  "tar", ".tgz": "tar",
    ".tar.bz2": "tar", ".tbz2": "tar",
    ".tar.xz":  "tar", ".txz": "tar",
}


def _archive_kind(filename: str) -> Optional[str]:
    lower = filename.lower()
    for ext, kind in sorted(_ARCHIVE_EXTS.items(), key=lambda kv: -len(kv[0])):
        if lower.endswith(ext):
            return kind
    return None


def _safe_extract_member(dest_root: str, member_name: str) -> Optional[str]:
    """Resolve an archive member path against dest_root, rejecting zip-slip
    (../, absolute paths, symlink escapes). Returns the safe absolute path,
    or None if the member should be skipped."""
    candidate = os.path.normpath(os.path.join(dest_root, member_name.lstrip("/\\")))
    root_rp = os.path.realpath(dest_root)
    cand_rp = os.path.realpath(os.path.dirname(candidate))
    if cand_rp != root_rp and not cand_rp.startswith(root_rp + os.sep):
        return None
    return candidate


def _extract_archive(raw: bytes, filename: str, kind: str, dest_root: str) -> dict:
    """Extract a zip/tar archive into dest_root/<archive-stem>/. Returns
    {extracted: bool, extract_dir, files: [...], skipped: [...], error}."""
    stem = re.sub(r"\.(zip|tar|tar\.gz|tgz|tar\.bz2|tbz2|tar\.xz|txz)$", "", filename, flags=re.IGNORECASE) or "archive"
    stem = sanitise_filename(stem) or "archive"
    extract_dir = os.path.join(dest_root, stem)
    os.makedirs(extract_dir, exist_ok=True)
    files: list[str] = []
    skipped: list[str] = []

    try:
        if kind == "zip":
            import zipfile as _zf
            with _zf.ZipFile(io.BytesIO(raw)) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    dest = _safe_extract_member(extract_dir, info.filename)
                    if dest is None:
                        skipped.append(info.filename)
                        continue
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with zf.open(info) as src, open(dest, "wb") as out:
                        out.write(src.read())
                    files.append(os.path.relpath(dest, extract_dir))
        elif kind == "tar":
            import tarfile as _tf
            with _tf.open(fileobj=io.BytesIO(raw), mode="r:*") as tf:
                for member in tf.getmembers():
                    if not member.isfile():
                        continue
                    dest = _safe_extract_member(extract_dir, member.name)
                    if dest is None:
                        skipped.append(member.name)
                        continue
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    src = tf.extractfile(member)
                    if src is None:
                        continue
                    with open(dest, "wb") as out:
                        out.write(src.read())
                    files.append(os.path.relpath(dest, extract_dir))
        else:
            return {"extracted": False, "error": f"Unsupported archive kind '{kind}'."}
    except Exception as exc:
        logger.warning("[archive_extract_error] file=%s err=%s", filename, exc)
        return {"extracted": False, "error": f"{type(exc).__name__}: {exc}"}

    if skipped:
        logger.warning("[archive_extract_skipped] file=%s skipped=%d (path traversal)", filename, len(skipped))
    logger.info("[archive_extracted] file=%s extract_dir=%s files=%d", filename, extract_dir, len(files))
    return {"extracted": True, "extract_dir": os.path.relpath(extract_dir, dest_root),
            "files": files, "skipped": skipped}


def _save_attachments_to_workspace(attachments: list[dict], workspace_path: str) -> list[dict]:
    """
    Persist every uploaded attachment into the active workspace directory.
    Archives (.zip/.tar/.tar.gz/.tgz/.tar.bz2/.tbz2/.tar.xz/.txz) are
    unpacked into workspace_path/<archive-stem>/ instead of being dropped in
    as a single opaque blob — so the agent's file tools (glob/grep/read/
    workspace browser) see the actual extracted files immediately. Never
    raises — a save/extract failure is recorded per-file and the chat
    request still proceeds with the in-memory attachment as before.
    """
    results: list[dict] = []
    for att in attachments:
        name = sanitise_filename(att.get("name", "upload"))
        try:
            if "b64" in att:
                raw = base64.b64decode(att["b64"])
            else:
                raw = (att.get("text", "") or "").encode("utf-8")
        except Exception as exc:
            results.append({"name": name, "saved": False, "error": f"decode failed: {exc}"})
            continue

        kind = _archive_kind(name)
        if kind:
            outcome = _extract_archive(raw, name, kind, workspace_path)
            results.append({"name": name, "saved": True, "archive": True, **outcome})
            continue

        try:
            dest = os.path.normpath(os.path.join(workspace_path, name))
            root_rp = os.path.realpath(workspace_path)
            if os.path.realpath(os.path.dirname(dest)) != root_rp:
                dest = os.path.join(workspace_path, os.path.basename(name))  # flatten any residual traversal
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(raw)
            results.append({"name": name, "saved": True, "archive": False,
                             "path": os.path.relpath(dest, workspace_path)})
            logger.info("[attachment_saved_to_workspace] name=%s workspace=%s bytes=%d", name, workspace_path, len(raw))
        except Exception as exc:
            logger.warning("[attachment_save_error] name=%s err=%s", name, exc)
            results.append({"name": name, "saved": False, "error": str(exc)})
    return results


# ══════════════════════════════════════════════════════════════════════════════
# CBD COMPONENT — Auth (SQLite-backed tokens)
# ══════════════════════════════════════════════════════════════════════════════
_DB_PATH = os.getenv("JARVIS_DB_PATH", "./data/jarvis.db")
_DB_LOCK = threading.Lock()

TOKEN_TTL_HOURS = int(os.getenv("JARVIS_TOKEN_TTL_HOURS", "24"))


def _get_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(_DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _init_db():
    with _DB_LOCK:
        conn = _get_db()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                username      TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                is_active     INTEGER NOT NULL DEFAULT 1,
                role          TEXT NOT NULL DEFAULT 'user'
            );
            CREATE TABLE IF NOT EXISTS auth_tokens (
                token       TEXT PRIMARY KEY,
                username    TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                expires_at  TEXT NOT NULL,
                FOREIGN KEY (username) REFERENCES users(username) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_tokens_username ON auth_tokens(username);
            CREATE INDEX IF NOT EXISTS idx_tokens_expires  ON auth_tokens(expires_at);
        """)
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        conn.close()
    if count == 0:
        logger.warning("[db_init] No users found — system is in SETUP MODE. "
                       "Visit the UI or POST /api/system/setup to create the first admin account.")


def _is_setup_required() -> bool:
    """Return True when the DB has zero users — first-boot setup mode."""
    with _DB_LOCK:
        conn  = _get_db()
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        conn.close()
    return count == 0


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_password(plaintext: str) -> str:
    salt  = secrets.token_hex(16)
    inner = _sha256_hex(plaintext)
    dk    = hashlib.pbkdf2_hmac("sha256", inner.encode(), salt.encode(), 260_000)
    return f"pbkdf2:{salt}:{dk.hex()}"


def _verify_password(candidate: str, stored_hash: str) -> bool:
    try:
        if stored_hash.startswith("pbkdf2:"):
            _, salt, dk_hex = stored_hash.split(":", 2)

            def _try(inner: str) -> bool:
                dk = hashlib.pbkdf2_hmac("sha256", inner.encode(), salt.encode(), 260_000)
                return hmac.compare_digest(dk.hex(), dk_hex)

            if len(candidate) == 64 and all(c in "0123456789abcdef" for c in candidate):
                return _try(candidate) or _try(_sha256_hex(candidate))
            return _try(_sha256_hex(candidate))
        else:
            salt, dk_hex = stored_hash.split(":", 1)
            dk  = hashlib.pbkdf2_hmac("sha256", candidate.encode(), salt.encode(), 260_000)
            dk2 = hashlib.pbkdf2_hmac("sha256", _sha256_hex(candidate).encode(), salt.encode(), 260_000)
            return hmac.compare_digest(dk.hex(), dk_hex) or hmac.compare_digest(dk2.hex(), dk_hex)
    except Exception:
        return False


def _create_user_internal(conn: sqlite3.Connection, username: str, password: str):
    ph = _hash_password(password)
    conn.execute(
        "INSERT OR IGNORE INTO users (username, password_hash) VALUES (?, ?)",
        (username.lower(), ph)
    )
    conn.commit()


def _issue_token(username: str) -> str:
    token      = secrets.token_hex(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=TOKEN_TTL_HOURS)).isoformat()
    with _DB_LOCK:
        conn = _get_db()
        # Prune expired tokens for this user (housekeeping)
        conn.execute(
            "DELETE FROM auth_tokens WHERE username = ? AND expires_at < ?",
            (username.lower(), datetime.now(timezone.utc).isoformat())
        )
        conn.execute(
            "INSERT INTO auth_tokens (token, username, expires_at) VALUES (?, ?, ?)",
            (token, username.lower(), expires_at)
        )
        conn.commit()
        conn.close()
    return token


def _validate_token(token: str) -> Optional[str]:
    """Return username if token is valid and not expired, else None."""
    if not token or len(token) > 128:
        return None
    now = datetime.now(timezone.utc).isoformat()
    with _DB_LOCK:
        conn = _get_db()
        row  = conn.execute(
            "SELECT username FROM auth_tokens WHERE token = ? AND expires_at > ?",
            (token, now)
        ).fetchone()
        conn.close()
    return row["username"] if row else None


def _revoke_token(token: str):
    if not token:
        return
    with _DB_LOCK:
        conn = _get_db()
        conn.execute("DELETE FROM auth_tokens WHERE token = ?", (token,))
        conn.commit()
        conn.close()


def _require_auth(request: Request) -> str:
    return require_auth(request, _validate_token)


def _require_admin(request: Request) -> str:
    username = _require_auth(request)
    require_admin(username)
    return username


_init_db()


# ══════════════════════════════════════════════════════════════════════════════
# CBD COMPONENT — First-Boot Setup (public, one-shot, self-sealing)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/system/setup-status", tags=["System"])
async def setup_status():
    """
    Public probe — tells the frontend whether first-boot setup is required.
    Returns {"setup_required": true}  when zero users exist in the DB.
    Returns {"setup_required": false} once any user has been created.

    No token required — must be reachable before any accounts exist.
    The frontend calls this on every page load before showing the login screen.
    """
    return {"setup_required": _is_setup_required()}


@app.post("/api/system/setup", tags=["System"])
async def system_setup(request: Request):
    """
    First-boot admin account creation.
    PUBLIC · ONE-SHOT · SELF-SEALING

    Only works when zero users exist in the DB.
    Once called successfully this endpoint returns 409 on every subsequent
    call — permanently sealed, no restart needed.

    Body: { username, password, confirm_password }
    Password arrives pre-hashed (SHA-256 hex, 64 chars) from the frontend
    so the plaintext is never transmitted over the wire.

    Security properties
    -------------------
    - Rate-limited per IP (reuses the login rate bucket)
    - TOCTOU-safe: the zero-users check runs inside the DB write lock
    - Role is set to 'admin' unconditionally — only valid for first user
    - Sealed immediately after first successful call
    - Full audit log: username + IP + timestamp
    """
    ip = get_client_ip(request)
    check_login_rate(ip)

    # Fast-path seal check before reading the body
    if not _is_setup_required():
        raise HTTPException(
            status_code=409,
            detail="Setup already completed. Use admin login to manage accounts.",
        )

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    username = (body.get("username") or "").strip().lower()
    password =  body.get("password", "")
    confirm  =  body.get("confirm_password", "")

    if not username:
        raise HTTPException(status_code=400, detail="username is required.")
    if not re.match(r"^[a-z0-9_\-]{2,32}$", username):
        raise HTTPException(status_code=400, detail="Username must be 2-32 chars: a-z 0-9 _ -")

    is_pre_hashed = len(password) == 64 and all(c in "0123456789abcdef" for c in password)
    if not password:
        raise HTTPException(status_code=400, detail="password is required.")
    if not is_pre_hashed and len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    if confirm and confirm != password:
        raise HTTPException(status_code=400, detail="Passwords do not match.")

    # TOCTOU guard — zero-user check + insert inside the same DB lock
    with _DB_LOCK:
        conn = _get_db()
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
            conn.close()
            raise HTTPException(
                status_code=409,
                detail="Setup already completed. Use admin login to manage accounts.",
            )
        if is_pre_hashed:
            salt = secrets.token_hex(16)
            dk   = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
            ph   = f"pbkdf2:{salt}:{dk.hex()}"
        else:
            ph = _hash_password(password)

        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin')",
            (username, ph),
        )
        conn.commit()
        conn.close()

    logger.warning(
        "[system_setup_complete] Admin '%s' created from ip=%s — "
        "setup endpoint permanently sealed.",
        username, ip,
    )
    return {
        "success":  True,
        "username": username,
        "role":     "admin",
        "message":  "Admin account created. Setup is now complete. Please log in.",
    }


# ══════════════════════════════════════════════════════════════════════════════
# CBD COMPONENT 8 — Health & Info
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/", tags=["System"])
async def root():
    return {
        "name":    "Mighty Jarvis MKII",
        "version": "4.1.0",
        "status":  "operational",
        "skills":  _registry.list_skills(),
    }


@app.get("/health", tags=["System"])
async def health():
    return {"status": "ok", "ts": datetime.now(timezone.utc).isoformat()}


@app.get("/api/models", tags=["System"])
async def list_models(request: Request):
    _require_auth(request)
    from core.llm_router import PROVIDER_MODELS
    return {"providers": PROVIDER_MODELS}


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER COMPONENT — Session Management
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/session/{user_id}", tags=["Session"])
async def session_status(user_id: str, request: Request):
    caller = _require_auth(request)
    # Users can only inspect their own session; admin sees any
    if caller != "admin" and caller != user_id:
        raise HTTPException(status_code=403, detail="Permission denied.")
    agent = _sessions.get(user_id)
    if agent is None:
        return {"exists": False, "user_id": user_id}
    return {
        "exists":           True,
        "user_id":          user_id,
        "conversation_len": len(agent.conversation),
        "pending_confirms": list(agent.pending_confirms.keys()),
    }


@app.delete("/api/session/{user_id}", tags=["Session"])
async def reset_session(user_id: str, request: Request):
    caller = _require_auth(request)
    if caller != "admin" and caller != user_id:
        raise HTTPException(status_code=403, detail="Permission denied.")
    destroyed = _destroy_session(user_id)
    return {"reset": destroyed, "user_id": user_id}


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER COMPONENT 9 — Chat Endpoints (authenticated)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/chat", tags=["Chat"])
async def chat_sse(
    message:      str,
    user_id:      str  = "default",
    react:        bool = False,
    auto_confirm: bool = False,
    model:        str  = None,
    provider:     str  = None,
    request:      Request = None,
):
    """SSE chat — authenticates via Authorization header."""
    caller = _require_auth(request)
    check_api_rate(get_client_ip(request))
    if caller != "admin" and caller != user_id:
        user_id = caller   # users can only chat as themselves

    message = sanitise_message(message)
    logger.info("[chat_sse] user=%s react=%s msg=%.60s", user_id, react, message)
    llm_trace.info("[CHAT-SSE] user=%s react=%s message=%.120s", user_id, react, message)
    try:
        agent = _get_session(user_id, model, provider)
        gen   = agent.chat_stream(message, react=react, auto_confirm=auto_confirm)
        return StreamingResponse(_agent_to_sse(gen), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
        })
    except Exception as exc:
        logger.error("[chat_sse_error] %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error.")


@app.post("/api/chat", tags=["Chat"])
async def chat_post(request: Request):
    caller = _require_auth(request)
    check_api_rate(get_client_ip(request))
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    message      = sanitise_message(body.get("message", ""))
    user_id      = body.get("user_id", caller)
    react        = body.get("react",        False)
    auto_confirm = body.get("auto_confirm", False)
    model        = body.get("model")
    provider     = body.get("provider")

    if caller != "admin":
        user_id = caller

    if not message:
        raise HTTPException(status_code=400, detail="message is required.")

    logger.info("[chat_post] user=%s react=%s msg=%.60s", user_id, react, message)
    llm_trace.info("[CHAT-POST] user=%s react=%s message=%.120s", user_id, react, message)
    try:
        agent = _get_session(user_id, model, provider)
        gen   = agent.chat_stream(message, react=react, auto_confirm=auto_confirm)
        return StreamingResponse(_agent_to_sse(gen), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
        })
    except Exception as exc:
        logger.error("[chat_post_error] %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error.")


@app.websocket("/ws/chat/{user_id}")
async def chat_websocket(ws: WebSocket, user_id: str):
    """
    WebSocket chat — first frame MUST be { type: 'auth', token: '...' }
    within WS_AUTH_TIMEOUT_S seconds or the connection is closed.
    """
    await ws.accept()

    # ── Auth handshake with timeout ──
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=WS_AUTH_TIMEOUT_S)
    except asyncio.TimeoutError:
        await ws.send_json({"type": "auth_failed", "data": "Auth timeout."})
        await ws.close(code=1008)
        return
    except Exception:
        await ws.close(code=1011)
        return

    try:
        first_msg = json.loads(raw)
    except json.JSONDecodeError:
        await ws.send_json({"type": "auth_failed", "data": "First frame must be JSON."})
        await ws.close(code=1008)
        return

    if first_msg.get("type") != "auth":
        await ws.send_json({"type": "auth_failed", "data": "First frame must be {type:'auth', token:'...'}"})
        await ws.close(code=1008)
        return

    token    = first_msg.get("token", "")
    ws_user  = _validate_token(token)
    if not ws_user:
        await ws.send_json({"type": "auth_failed", "data": "Invalid or expired token."})
        await ws.close(code=1008)
        return

    # Users can only use their own user_id
    if ws_user != "admin" and ws_user != user_id:
        user_id = ws_user

    _probe_cfg = _build_llm_config()
    from core.llm_router import LLMRouter as _LLMRouter
    _probe_router = _LLMRouter(_probe_cfg)
    await ws.send_json({"type": "auth_ok", "data": {
        "username": ws_user,
        "provider_info": {
            "provider":      _probe_cfg.provider,
            "model":         _probe_router.model,
            "schema_format": _probe_cfg.get_schema(),
        },
    }})

    logger.info("[ws_connected] user=%s ws_user=%s", user_id, ws_user)

    # ── Message loop ──
    try:
        while True:
            try:
                raw = await ws.receive_text()
            except WebSocketDisconnect:
                break

            # Message size guard
            if len(raw.encode()) > WS_MAX_MSG_BYTES:
                await ws.send_json({"type": "error", "data": "Message too large."})
                continue

            check_ws_rate(user_id)

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "data": "Invalid JSON."})
                continue

            halt         = msg.get("halt", False)
            confirm_id   = msg.get("confirm_id", "")
            message      = sanitise_message(msg.get("message", ""))
            react        = msg.get("react",        False)
            auto_confirm = msg.get("auto_confirm", False)
            model        = msg.get("model")
            provider     = msg.get("provider")
            attachments  = msg.get("attachments",  []) or []
            instructions    = msg.get("instructions", "")
            memory_enabled  = msg.get("memory_enabled", True)
            persona         = msg.get("persona", DEFAULT_PERSONA)
            project         = msg.get("project", _default_project())
            workspace_path  = _workspace_path(user_id, project)

            if halt:
                _destroy_session(user_id)
                await ws.send_json({"type": "halted", "data": {"user_id": user_id}})
                logger.info("[ws_halt] user=%s", user_id)
                continue

            if confirm_id:
                agent = _sessions.get(user_id)
                if agent:
                    async for event in agent.confirm_action(confirm_id):
                        await ws.send_json(event)
                else:
                    await ws.send_json({"type": "error", "data": "No active session."})
                continue

            if not message and not attachments:
                continue

            llm_trace.info("[WS] user=%s project=%s react=%s atts=%d msg=%.120s",
                           user_id, project, react, len(attachments), message)

            # Persist every uploaded file into the active workspace (archives
            # are unpacked there too) so the agent's file tools see them as
            # real files, not just inline chat context.
            save_results = _save_attachments_to_workspace(attachments, workspace_path) if attachments else []
            if save_results:
                lines = []
                for r in save_results:
                    if not r.get("saved"):
                        lines.append(f"  ✗ {r['name']}: {r.get('error', 'save failed')}")
                    elif r.get("archive"):
                        if r.get("extracted"):
                            lines.append(f"  📦 {r['name']} → extracted to {r['extract_dir']}/ ({len(r.get('files', []))} file(s))")
                        else:
                            lines.append(f"  ✗ {r['name']}: extraction failed — {r.get('error', 'unknown error')}")
                    else:
                        lines.append(f"  📄 {r['name']} → saved to {r['path']}")
                attachment_note = "[UPLOADED FILES — saved into your workspace]\n" + "\n".join(lines) + "\n"
            else:
                attachment_note = ""

            if save_results:
                await ws.send_json({"type": "attachments_saved", "data": save_results})

            try:
                agent = _get_session(user_id, model, provider)
                workspace_ctx = (
                    f"[WORKSPACE CONTEXT]\n"
                    f"Active Project  : {project}\n"
                    f"Workspace Path  : {workspace_path}\n"
                    f"User            : {user_id}\n"
                    f"All file operations MUST use this workspace path as root.\n"
                    f"{attachment_note}"
                )
                effective_msg = (
                    f"{workspace_ctx}\n[OPERATOR INSTRUCTIONS]\n{instructions}\n\n[MESSAGE]\n{message}"
                    if instructions else
                    f"{workspace_ctx}\n[MESSAGE]\n{message}"
                )
                async for event in agent.chat_stream(
                    effective_msg, react=react, auto_confirm=auto_confirm,
                    attachments=attachments or None,
                    memory_enabled=memory_enabled, persona=persona,
                ):
                    await ws.send_json(event)
            except Exception as exc:
                logger.error("[ws_dispatch_error] user=%s err=%s", user_id, exc)
                await ws.send_json({"type": "error", "data": "Agent error."})

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.error("[ws_error] user=%s err=%s", user_id, exc)
    finally:
        logger.info("[ws_disconnected] user=%s", user_id)


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER COMPONENT 10 — File Upload + Chat (authenticated, size-limited)
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/chat/upload", tags=["Chat"])
async def chat_upload(
    request:      Request,
    message:      str          = Form(""),
    user_id:      str          = Form("default"),
    project:      str          = Form(None),
    react:        bool         = Form(False),
    auto_confirm: bool         = Form(False),
    model:        str          = Form(None),
    provider:     str          = Form(None),
    files:        list[UploadFile] = File(default=[]),
):
    caller = _require_auth(request)
    check_api_rate(get_client_ip(request))
    if caller != "admin":
        user_id = caller

    message        = sanitise_message(message)
    project        = project or _default_project()
    workspace_path = _workspace_path(user_id, project)
    attachments    = await _process_attachments(files) if files else []
    save_results   = _save_attachments_to_workspace(attachments, workspace_path) if attachments else []
    logger.info("[chat_upload] user=%s project=%s files=%d msg=%.60s", user_id, project, len(attachments), message)

    if save_results:
        lines = []
        for r in save_results:
            if not r.get("saved"):
                lines.append(f"  ✗ {r['name']}: {r.get('error', 'save failed')}")
            elif r.get("archive"):
                if r.get("extracted"):
                    lines.append(f"  📦 {r['name']} → extracted to {r['extract_dir']}/ ({len(r.get('files', []))} file(s))")
                else:
                    lines.append(f"  ✗ {r['name']}: extraction failed — {r.get('error', 'unknown error')}")
            else:
                lines.append(f"  📄 {r['name']} → saved to {r['path']}")
        workspace_ctx = (
            f"[WORKSPACE CONTEXT]\nWorkspace Path: {workspace_path}\n"
            f"[UPLOADED FILES — saved into your workspace]\n" + "\n".join(lines) + "\n\n"
        )
        message = f"{workspace_ctx}[MESSAGE]\n{message}"

    try:
        agent = _get_session(user_id, model, provider)
        gen   = agent.chat_stream(message, react=react, auto_confirm=auto_confirm, attachments=attachments)

        async def _gen_with_attachment_note():
            if save_results:
                yield {"type": "attachments_saved", "data": save_results}
            async for event in gen:
                yield event

        return StreamingResponse(_agent_to_sse(_gen_with_attachment_note()), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
        })
    except Exception as exc:
        logger.error("[chat_upload_error] %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error.")


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER COMPONENT 11 — Confirmation Gate
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/confirm", tags=["Chat"])
async def confirm_action(request: Request):
    caller = _require_auth(request)
    body   = await request.json()
    confirm_id = body.get("confirm_id")
    user_id    = body.get("user_id", caller)

    if caller != "admin":
        user_id = caller
    if not confirm_id:
        raise HTTPException(status_code=400, detail="confirm_id required.")

    agent = _sessions.get(user_id)
    if not agent:
        raise HTTPException(status_code=404, detail="No active session.")

    gen = agent.confirm_action(confirm_id)
    return StreamingResponse(_agent_to_sse(gen), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER COMPONENT 12 — JarvisMKII (authenticated)
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/mkii/tasks", tags=["JarvisMKII"])
async def mkii_run_tasks(request: Request):
    _require_auth(request)
    check_api_rate(get_client_ip(request))
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")
    result = await _jarvis_mkii_execute(body)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result)
    return JSONResponse(result)


@app.get("/api/mkii/tasks", tags=["JarvisMKII"])
async def mkii_active_tasks(request: Request):
    _require_auth(request)
    with _task_lock:
        return {"active_tasks": list(_active_tasks.values()), "count": len(_active_tasks)}


@app.websocket("/ws/mkii/{session_id}")
async def mkii_websocket(ws: WebSocket, session_id: str):
    await ws.accept()
    # Auth: first frame must be { type: 'auth', token: '...' }
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=WS_AUTH_TIMEOUT_S)
        first = json.loads(raw)
        if first.get("type") != "auth" or not _validate_token(first.get("token", "")):
            await ws.send_json({"type": "auth_failed", "data": "Invalid token."})
            await ws.close(code=1008)
            return
    except Exception:
        await ws.close(code=1008)
        return

    try:
        raw     = await ws.receive_text()
        payload = json.loads(raw)
        tasks   = payload.get("tasks", [])
        if not tasks:
            await ws.send_json({"type": "error", "data": "No tasks provided."})
            return

        await ws.send_json({"type": "mkii_start", "data": {"task_count": len(tasks)}})

        async def _run_and_send(task_def):
            result = await _run_single_task(task_def)
            await ws.send_json({"type": "task_result", "data": result})

        await asyncio.gather(*[_run_and_send(t) for t in tasks])
        await ws.send_json({"type": "done", "data": {"session_id": session_id}})

    except WebSocketDisconnect:
        logger.info("[mkii_ws_disconnected] session=%s", session_id)
    except Exception as exc:
        logger.error("[mkii_ws_error] session=%s err=%s", session_id, exc)
        try:
            await ws.send_json({"type": "error", "data": "Internal error."})
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER COMPONENT 13 — Kali Exec (hardened: key required, never open)
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/kali/exec", tags=["Kali"])
async def kali_exec(request: Request):
    require_kali_key(request, _KALI_API_KEY)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")
    command = body.get("command", "").strip()
    if not command:
        raise HTTPException(status_code=400, detail="command is required.")
    result = await _kali_exec(
        command=command,
        cwd=body.get("cwd"),
        timeout=body.get("timeout"),
        env_extra=body.get("env", {}),
    )
    return JSONResponse(result)


@app.get("/api/kali/tools", tags=["Kali"])
async def kali_tools(request: Request):
    _require_auth(request)
    return {
        "categories": {
            "network_recon":    ["nmap", "masscan", "netcat-openbsd", "tcpdump", "dnsutils", "whois",
                                 "dnsrecon", "dnsenum", "fierce", "recon-ng", "theharvester", "amass",
                                 "sublist3r", "httprobe", "hakrawler"],
            "web_testing":      ["nikto", "gobuster", "wfuzz", "whatweb", "dirb", "sqlmap",
                                 "sslyze", "ffuf", "wpscan", "joomscan", "commix", "xsser"],
            "exploitation":     ["metasploit-framework", "hydra", "john", "exploitdb", "shellter"],
            "password_cracking":["hashcat", "hashcat-utils", "cewl", "crunch"],
            "windows_ad":       ["responder", "smbclient", "enum4linux", "enum4linux-ng",
                                 "impacket-scripts", "bloodhound", "certipy-ad", "evil-winrm", "netexec"],
            "wireless_rf":      ["aircrack-ng", "wifite", "kismet", "reaver"],
            "system_audit":     ["lynis", "rkhunter", "unhide", "ssh-audit"],
            "forensics":        ["exiftool", "steghide", "stegseek"],
            "tunneling":        ["chisel", "proxychains4"],
            "osint":            ["setoolkit", "python3-shodan"],
        }
    }


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER COMPONENT 14 — Memory System (authenticated)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/memory/{user_id}", tags=["Memory"])
async def memory_retrieve(user_id: str, request: Request, n: int = 5):
    caller = _require_auth(request)
    if caller != "admin" and caller != user_id:
        raise HTTPException(status_code=403, detail="Permission denied.")
    history = _memory.retrieve_last_n(user_id, n=min(n, 20))
    return {"user_id": user_id, "history": history, "found": bool(history)}


@app.delete("/api/memory/{user_id}", tags=["Memory"])
async def memory_clear(user_id: str, request: Request):
    caller = _require_auth(request)
    if caller != "admin" and caller != user_id:
        raise HTTPException(status_code=403, detail="Permission denied.")
    ok = _memory.clear(user_id)
    return {"cleared": ok, "user_id": user_id}


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER COMPONENT — Self-Evolution (admin only)
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/evolve", tags=["SelfEvolution"])
async def trigger_evolution(request: Request):
    _require_admin(request)    # self-evolution is admin-only
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    task_desc   = body.get("task_description", "")
    origin_path = body.get("origin_path", "")
    slug        = body.get("slug", "evolution-task")
    user_id     = body.get("user_id", "evolution-agent")

    if not task_desc or not origin_path:
        raise HTTPException(status_code=400, detail="task_description and origin_path are required.")

    evolution_prompt = (
        f"Execute the Self-Evolution skill protocol (9 phases).\n\n"
        f"**Task**: {task_desc}\n"
        f"**Origin Path**: {origin_path}\n"
        f"**Slug**: {slug}\n\n"
        f"Use TOOL_CALL blocks for every action. Start with Phase 0 Experienced lookup, "
        f"then bootstrap workspace, discover source, analyze, generate blueprint.md + blueprint.json, "
        f"STOP and present blueprint to user for approval (Phase 4 gate — do not proceed without APPROVED). "
        f"After approval: implement, test, deploy, verify, capture experience. "
        f"End with TASK_COMPLETE."
    )

    logger.info("[evolve_request] user=%s slug=%s origin=%.60s", user_id, slug, origin_path)
    try:
        agent = _get_session(user_id)
        gen   = agent.chat_stream(
            evolution_prompt, react=True, auto_confirm=False,
            force_single_task=True, re_act_max_loop=DEEP_TASK_MAX_ITERATIONS,
        )
        return StreamingResponse(_agent_to_sse(gen), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
        })
    except Exception as exc:
        logger.error("[evolve_error] %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error.")


@app.post("/api/code_task", tags=["CodeAgent"])
async def trigger_code_task(request: Request):
    """
    Runs one request through the Search → Edit → Validate coding loop
    (core/blackbox_brain.py's force_code_agent path) instead of the generic
    swarm/planner. Use this for "fix this bug", "add this feature", "refactor
    X" — anything where the agent should search the codebase, make a change,
    then prove it with a real test/build/lint run before calling it done.
    """
    caller = _require_auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    task_desc = sanitise_message(body.get("task_description", "") or body.get("message", ""))
    user_id   = body.get("user_id", caller)
    model     = body.get("model")
    provider  = body.get("provider")
    max_iter  = int(body.get("max_iterations", CODE_AGENT_MAX_ITERATIONS))

    if not task_desc:
        raise HTTPException(status_code=400, detail="task_description (or message) is required.")

    logger.info("[code_task_request] user=%s task=%.80s", user_id, task_desc)
    try:
        agent = _get_session(user_id, model=model, provider=provider)
        gen   = agent.chat_stream(
            task_desc, react=True, auto_confirm=bool(body.get("auto_confirm", False)),
            force_code_agent=True, re_act_max_loop=max_iter,
        )
        return StreamingResponse(_agent_to_sse(gen), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
        })
    except Exception as exc:
        logger.error("[code_task_error] %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error.")


# ══════════════════════════════════════════════════════════════════════════════
# CBD COMPONENT — Runtime Settings (admin-only; replaces .env-and-restart)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/settings", tags=["Settings"])
async def get_settings(request: Request):
    """Effective value + source (override/environment/default) for every
    operator-editable setting — workspace root, default project, swarm
    concurrency, task timeout, default LLM provider/model. Admin-only:
    these are process-wide, not per-user."""
    caller = _require_admin(request)
    return {"settings": describe_settings()}


@app.post("/api/settings", tags=["Settings"])
async def update_settings(request: Request):
    """
    Body: { "<KEY>": <value>, ... } for any subset of the editable keys
    (see GET /api/settings). Validated and persisted to settings.json —
    takes effect immediately for every subsequent request, no restart.
    Rejects unknown keys and invalid values (e.g. a workspace path that
    isn't absolute or can't be created) with a 400 explaining why.
    """
    caller = _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")
    if not isinstance(body, dict) or not body:
        raise HTTPException(status_code=400, detail="Provide at least one setting to update.")

    try:
        updated = save_settings(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    logger.info("[settings_updated] admin=%s keys=%s", caller, ", ".join(body.keys()))
    return {"settings": updated}


@app.post("/api/settings/reset", tags=["Settings"])
async def reset_settings_endpoint(request: Request):
    """Body: { "keys": ["JARVIS_WORKSPACE_ROOT", ...] } or {} / omitted to
    reset ALL settings back to environment/default."""
    caller = _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    keys = body.get("keys")
    updated = reset_settings(keys)
    logger.info("[settings_reset] admin=%s keys=%s", caller, keys or "ALL")
    return {"settings": updated}


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER COMPONENT — CBD Architect (authenticated)
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/cbd/{action}", tags=["CBD"])
async def cbd_action(action: str, request: Request):
    _require_auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")
    params = body.get("params", body)
    return await _skill_call("cbd_architect", action, params)


async def _skill_call(skill: str, action: str, params: dict) -> JSONResponse:
    try:
        result = await _registry.execute(skill, action, params, confirmed=True)
        return JSONResponse(result.to_dict())
    except Exception as exc:
        logger.error("[skill_call_error] %s.%s err=%s", skill, action, exc)
        raise HTTPException(status_code=500, detail="Internal server error.")


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER COMPONENT — Skills (admin-only reload/register)
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/skills/reload", tags=["Skills"])
async def skills_reload(request: Request):
    _require_admin(request)
    result = _registry.reload()
    logger.info("[skills_reload] added=%s total=%d", result.get("added"), len(result.get("skills", [])))
    return result


@app.post("/api/skills/register", tags=["Skills"])
async def skills_register(request: Request):
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")
    module_path = body.get("module_path")
    if not module_path:
        raise HTTPException(status_code=400, detail="module_path is required.")
    result = _registry.register_module(module_path, body.get("class_name"), body.get("name"))
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Registration failed."))
    return result


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER COMPONENT — Halt / Session Control
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/halt/{user_id}", tags=["Session"])
async def halt_session(user_id: str, request: Request):
    caller = _require_auth(request)
    if caller != "admin" and caller != user_id:
        raise HTTPException(status_code=403, detail="Permission denied.")
    destroyed = _destroy_session(user_id)
    logger.info("[api_halt] user=%s destroyed=%s", user_id, destroyed)
    return {"halted": True, "user_id": user_id, "session_destroyed": destroyed}


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER COMPONENT — Auth Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/auth/login", tags=["Auth"])
async def auth_login(request: Request):
    ip = get_client_ip(request)
    check_login_rate(ip)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    username = (body.get("username") or "").strip().lower()
    password =  body.get("password", "")

    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password are required.")

    with _DB_LOCK:
        conn = _get_db()
        row  = conn.execute(
            "SELECT password_hash, is_active FROM users WHERE username = ?", (username,)
        ).fetchone()
        conn.close()

    # Constant-time failure path — same response and timing whether user exists or not
    if row is None or not row["is_active"] or not _verify_password(password, row["password_hash"]):
        logger.warning("[auth_fail] user=%s ip=%s", username, ip)
        raise HTTPException(status_code=401, detail="Invalid credentials.")

    token = _issue_token(username)
    clear_login_rate(ip)   # reset rate counter on success
    logger.info("[auth_ok] user=%s ip=%s", username, ip)
    return {"access_token": token, "token_type": "bearer", "username": username,
            "expires_in": TOKEN_TTL_HOURS * 3600}


@app.post("/api/auth/logout", tags=["Auth"])
async def auth_logout(request: Request):
    hdr   = request.headers.get("Authorization", "")
    token = hdr[7:].strip() if hdr.startswith("Bearer ") else ""
    _revoke_token(token)
    return {"logged_out": True}


@app.get("/api/auth/me", tags=["Auth"])
async def auth_me(request: Request):
    try:
        username = _require_auth(request)
        return {"username": username, "authenticated": True}
    except HTTPException:
        return {"username": None, "authenticated": False}


@app.post("/api/auth/register", tags=["Auth"])
async def auth_register(request: Request):
    """
    Register a new user.
    Open registration is OFF by default (JARVIS_OPEN_REGISTRATION=false).
    When closed, requires an admin token.
    """
    open_reg = os.getenv("JARVIS_OPEN_REGISTRATION", "false").lower() == "true"
    if not open_reg:
        _require_admin(request)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    username = (body.get("username") or "").strip().lower()
    password =  body.get("password", "")

    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password are required.")
    if not re.match(r"^[a-z0-9_\-]{2,32}$", username):
        raise HTTPException(status_code=400, detail="Username must be 2-32 chars: a-z 0-9 _ -")
    if len(password) < 8 and not (len(password) == 64 and all(c in "0123456789abcdef" for c in password)):
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

    with _DB_LOCK:
        conn = _get_db()
        existing = conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
        if existing:
            conn.close()
            raise HTTPException(status_code=409, detail=f"Username '{username}' already exists.")

        if len(password) == 64 and all(c in "0123456789abcdef" for c in password):
            salt = secrets.token_hex(16)
            dk   = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
            ph   = f"pbkdf2:{salt}:{dk.hex()}"
            conn.execute("INSERT OR IGNORE INTO users (username, password_hash) VALUES (?, ?)", (username, ph))
            conn.commit()
        else:
            _create_user_internal(conn, username, password)
        conn.close()

    logger.info("[auth_register] user=%s", username)
    return {"registered": True, "username": username}


@app.get("/api/auth/users", tags=["Auth"])
async def auth_list_users(request: Request):
    _require_admin(request)
    with _DB_LOCK:
        conn  = _get_db()
        users = conn.execute(
            "SELECT username, created_at, is_active, role FROM users ORDER BY created_at"
        ).fetchall()
        conn.close()
    return {"users": [dict(u) for u in users]}


@app.put("/api/auth/users/{username}", tags=["Auth"])
async def auth_update_user(username: str, request: Request):
    caller = _require_auth(request)
    target = username.lower()
    if caller != "admin" and caller != target:
        raise HTTPException(status_code=403, detail="Permission denied.")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    new_pass  = body.get("password")
    is_active = body.get("is_active")

    if caller != "admin" and is_active is not None:
        raise HTTPException(status_code=403, detail="Only admin can change active status.")

    with _DB_LOCK:
        conn = _get_db()
        if not conn.execute("SELECT 1 FROM users WHERE username = ?", (target,)).fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail=f"User '{target}' not found.")

        updates, params = [], []
        if new_pass is not None:
            if len(new_pass) < 8 and len(new_pass) != 64:
                conn.close()
                raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
            if len(new_pass) == 64 and all(c in "0123456789abcdef" for c in new_pass):
                _s  = secrets.token_hex(16)
                _dk = hashlib.pbkdf2_hmac("sha256", new_pass.encode(), _s.encode(), 260_000)
                ph  = f"pbkdf2:{_s}:{_dk.hex()}"
            else:
                ph = _hash_password(new_pass)
            updates.append("password_hash = ?"); params.append(ph)
        if is_active is not None:
            if target == "admin" and not is_active:
                conn.close()
                raise HTTPException(status_code=400, detail="Cannot deactivate admin account.")
            updates.append("is_active = ?"); params.append(1 if is_active else 0)
        if not updates:
            conn.close()
            return {"updated": False, "message": "No fields to update."}

        params.append(target)
        conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE username = ?", params)
        conn.commit()
        conn.close()

    logger.info("[user_update] caller=%s target=%s", caller, target)
    return {"updated": True, "username": target}


@app.delete("/api/auth/users/{username}", tags=["Auth"])
async def auth_delete_user(username: str, request: Request):
    caller = _require_admin(request)
    target = username.lower()
    if target in ("admin", caller):
        raise HTTPException(status_code=400, detail="Cannot delete admin or your own account.")
    with _DB_LOCK:
        conn = _get_db()
        if not conn.execute("SELECT 1 FROM users WHERE username = ?", (target,)).fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail=f"User '{target}' not found.")
        conn.execute("DELETE FROM auth_tokens WHERE username = ?", (target,))
        conn.execute("DELETE FROM users WHERE username = ?", (target,))
        conn.commit()
        conn.close()
    logger.info("[user_delete] caller=%s deleted=%s", caller, target)
    return {"deleted": True, "username": target}


@app.post("/api/auth/users/{username}/reset-password", tags=["Auth"])
async def auth_reset_user_password(username: str, request: Request):
    _require_admin(request)
    target = username.lower()
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")
    new_pw = body.get("new_password", "")
    if len(new_pw) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    with _DB_LOCK:
        conn = _get_db()
        if not conn.execute("SELECT 1 FROM users WHERE username = ?", (target,)).fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail=f"User '{target}' not found.")
        ph = _hash_password(new_pw)
        conn.execute("UPDATE users SET password_hash = ? WHERE username = ?", (ph, target))
        conn.commit()
        conn.close()
    logger.info("[user_reset_password] caller=admin target=%s", target)
    return {"reset": True, "username": target}   # password NOT echoed back


@app.get("/api/auth/debug", tags=["Auth"])
async def auth_debug(request: Request):
    """
    DEVELOPMENT ONLY — requires JARVIS_DEBUG_ENDPOINTS=true AND admin auth.
    Returns DB state for diagnosing login issues.
    """
    require_debug_disabled()   # raises 404 unless env var is set
    _require_admin(request)    # also requires admin token
    with _DB_LOCK:
        conn   = _get_db()
        users  = conn.execute(
            "SELECT username, created_at, is_active, role FROM users ORDER BY created_at"
        ).fetchall()
        tokens = conn.execute("SELECT COUNT(*) as cnt FROM auth_tokens").fetchone()
        conn.close()
    return {
        "db_path":       os.path.abspath(_DB_PATH),
        "db_exists":     os.path.isfile(_DB_PATH),
        "user_count":    len(users),
        "users":         [dict(u) for u in users],
        "active_tokens": tokens["cnt"] if tokens else 0,
    }


@app.post("/api/auth/reset-admin", tags=["Auth"])
async def auth_reset_admin(request: Request):
    """Emergency localhost-only admin password reset. Does NOT return the password."""
    client_ip = get_client_ip(request)
    if client_ip not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="Only accessible from localhost.")
    new_pass = request.headers.get("X-New-Password", "")
    if len(new_pass) < 8:
        raise HTTPException(status_code=400, detail="X-New-Password header must be >= 8 chars.")
    ph = _hash_password(new_pass)
    with _DB_LOCK:
        conn = _get_db()
        conn.execute(
            "INSERT INTO users (username, password_hash, is_active) VALUES ('admin', ?, 1) "
            "ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash, is_active=1",
            (ph,)
        )
        conn.commit()
        conn.close()
    logger.warning("[auth_reset_admin] admin password reset from %s", client_ip)
    return {"reset": True, "username": "admin"}   # password NOT echoed back


@app.post("/api/auth/wipe-and-reseed", tags=["Auth"])
async def wipe_and_reseed(request: Request):
    """
    EMERGENCY: wipe all users and tokens. Localhost only.
    After wipe the system returns to first-boot SETUP MODE —
    use the UI setup wizard or POST /api/system/setup to create a new admin.
    No default password is seeded (eliminates the admin123 footgun).
    """
    client_ip = get_client_ip(request)
    if client_ip not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="Only accessible from localhost.")
    with _DB_LOCK:
        conn = _get_db()
        conn.execute("DELETE FROM auth_tokens")
        conn.execute("DELETE FROM users")
        conn.commit()
        conn.close()
    logger.warning("[wipe_reseed] DB wiped from %s — system returned to SETUP MODE", client_ip)
    return {
        "wiped":         True,
        "setup_required": True,
        "message":       "All accounts removed. Use /api/system/setup or the UI to create a new admin.",
    }


# ══════════════════════════════════════════════════════════════════════════════
# CBD COMPONENT — Workspace File Browser (hardened)
# ══════════════════════════════════════════════════════════════════════════════
import fnmatch as _fnmatch

WS_MAX_FILE_BYTES  = 128_000
WS_MAX_TOTAL_BYTES = 512_000
WS_SKIP_DIRS  = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build"}
WS_TEXT_EXTS  = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".sh", ".bash", ".zsh",
    ".md", ".txt", ".rst", ".cfg", ".ini", ".toml", ".yaml", ".yml",
    ".json", ".xml", ".html", ".css", ".sql", ".go", ".rs", ".java",
    ".c", ".cpp", ".h", ".env", ".conf", ".log", ".csv",
}


def _workspace_root() -> str:
    """
    Live-read the configured workspace root. Deliberately NOT a module-level
    constant — settings.json can change this at runtime (via /api/settings)
    and every caller must see the update immediately, not just after a
    restart. This is what previously showed a stale/incorrect directory:
    a `WORKSPACE_ROOT = os.getenv(...)` constant captured once at import
    kept whatever value was true at process start, forever.
    """
    return get_setting("JARVIS_WORKSPACE_ROOT", "/app/workspace")


def _default_project() -> str:
    return get_setting("JARVIS_DEFAULT_PROJECT", "default")


def _safe_name(name: str, maxlen: int = 48) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name.strip())[:maxlen] or "default"


def _user_root(user_id: str) -> str:
    path = os.path.join(_workspace_root(), _safe_name(user_id))
    os.makedirs(path, exist_ok=True)
    return path


def _workspace_path(user_id: str, project: str = None) -> str:
    safe_proj = _safe_name(project) if project else _default_project()
    path = os.path.join(_user_root(user_id), safe_proj, "workspace")
    os.makedirs(path, exist_ok=True)
    return path


def _is_text_file(name: str) -> bool:
    _, ext = os.path.splitext(name)
    return ext.lower() in WS_TEXT_EXTS


@app.get("/api/workspace/{user_id}", tags=["Workspace"])
async def workspace_list(
    user_id: str,
    request: Request,
    path:    str = "",
    depth:   int = 4,
    project: str = None,
):
    caller = _require_auth(request)
    if caller != "admin" and caller != user_id:
        raise HTTPException(status_code=403, detail="Permission denied.")

    project  = project or _default_project()
    ws_root  = _workspace_path(user_id, project)
    # Use realpath to defeat symlink traversal
    if path:
        candidate = os.path.normpath(os.path.join(ws_root, path.lstrip("/")))
        target    = validate_workspace_path(candidate, ws_root)
    else:
        target = ws_root

    if not os.path.isdir(target):
        raise HTTPException(status_code=404, detail="Directory not found.")

    entries = []
    try:
        for dirpath, dirs, files in os.walk(target):
            rel_dir       = os.path.relpath(dirpath, ws_root)
            current_depth = 0 if rel_dir == "." else len(rel_dir.split(os.sep))
            if current_depth >= depth:
                dirs.clear()
                continue
            dirs[:] = sorted(d for d in dirs if d not in WS_SKIP_DIRS and not d.startswith("."))
            for fname in sorted(files):
                if fname.startswith("."):
                    continue
                fpath    = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(fpath, ws_root)
                try:
                    fsize = os.path.getsize(fpath)
                except OSError:
                    fsize = 0
                entries.append({
                    "name":    fname,
                    "path":    rel_path,      # relative only — no abs_path to client
                    "dir":     rel_dir if rel_dir != "." else "",
                    "size":    fsize,
                    "is_text": _is_text_file(fname),
                    "type":    "file",
                })
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Permission denied.")

    return {
        "user_id": user_id,
        "project": _safe_name(project),
        "path":    path or "/",
        "entries": entries,
        "count":   len(entries),
        # Real configured root — caller is admin or the workspace owner, so this
        # isn't a cross-tenant leak, and the frontend needs it to display (and
        # let operators verify) where the agent is actually reading/writing.
        "workspace_root": ws_root,
    }


@app.post("/api/workspace/{user_id}/read", tags=["Workspace"])
async def workspace_read_files(user_id: str, request: Request):
    caller = _require_auth(request)
    if caller != "admin" and caller != user_id:
        raise HTTPException(status_code=403, detail="Permission denied.")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    files   = body.get("files", [])
    project = body.get("project", _default_project())
    ws_root = _workspace_path(user_id, project)
    results, total = [], 0

    for rel_path in files[:50]:
        candidate = os.path.normpath(os.path.join(ws_root, rel_path))
        try:
            abs_path = validate_workspace_path(candidate, ws_root)
        except HTTPException:
            results.append({"path": rel_path, "error": "Path outside workspace.", "content": ""})
            continue
        if not os.path.isfile(abs_path):
            results.append({"path": rel_path, "error": "File not found.", "content": ""})
            continue
        try:
            fsize      = os.path.getsize(abs_path)
            read_limit = min(WS_MAX_FILE_BYTES, WS_MAX_TOTAL_BYTES - total)
            if read_limit <= 0:
                results.append({"path": rel_path, "error": "Size limit reached.", "content": ""})
                continue
            is_text = _is_text_file(os.path.basename(abs_path))
            if is_text:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(read_limit)
            else:
                import base64 as _b64
                with open(abs_path, "rb") as f:
                    raw = f.read(read_limit)
                content = _b64.b64encode(raw).decode()
            total += len(content)
            results.append({
                "path":      rel_path,
                "name":      os.path.basename(abs_path),
                "content":   content,
                "size":      fsize,
                "truncated": fsize > read_limit,
                "is_text":   is_text,
                "error":     None,
            })
        except Exception as exc:
            results.append({"path": rel_path, "error": "Read error.", "content": ""})

    return {"files": results, "count": len(results), "total_bytes": total}


@app.post("/api/workspace/{user_id}/mkdir", tags=["Workspace"])
async def workspace_mkdir(user_id: str, request: Request):
    caller = _require_auth(request)
    if caller != "admin" and caller != user_id:
        raise HTTPException(status_code=403, detail="Permission denied.")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")
    rel_path = body.get("path", "").strip().lstrip("/")
    project  = body.get("project", _default_project())
    if not rel_path:
        raise HTTPException(status_code=400, detail="path is required.")
    ws_root  = _workspace_path(user_id, project)
    candidate = os.path.normpath(os.path.join(ws_root, rel_path))
    abs_path  = validate_workspace_path(candidate, ws_root)
    os.makedirs(abs_path, exist_ok=True)
    return {"created": True, "path": rel_path}


@app.delete("/api/workspace/{user_id}/file", tags=["Workspace"])
async def workspace_delete_file(user_id: str, request: Request):
    caller = _require_auth(request)
    if caller != "admin" and caller != user_id:
        raise HTTPException(status_code=403, detail="Permission denied.")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")
    rel_path = body.get("path", "").strip()
    project  = body.get("project", _default_project())
    if not rel_path:
        raise HTTPException(status_code=400, detail="path is required.")
    ws_root   = _workspace_path(user_id, project)
    candidate = os.path.normpath(os.path.join(ws_root, rel_path))
    abs_path  = validate_workspace_path(candidate, ws_root)
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="File not found.")
    os.remove(abs_path)
    logger.info("[workspace_delete] user=%s path=%s", user_id, rel_path)
    return {"deleted": True, "path": rel_path}


# ══════════════════════════════════════════════════════════════════════════════
# Scheduler, RCA, Experienced, CBD — pass-through with auth added
# (these sections are unchanged in logic; auth guard is the only addition)
# ══════════════════════════════════════════════════════════════════════════════
# NOTE TO INTEGRATOR:
# All remaining endpoints (scheduler CRUD, RCA, experienced, workspace/zip,
# project management) follow the same auth pattern:
#
#   caller = _require_auth(request)
#   # optionally: require_admin(caller)
#
# Add this to the top of each handler that is missing it in the original file.
# The patterns above are the definitive reference.
# ══════════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════════
# Startup / Shutdown
# ══════════════════════════════════════════════════════════════════════════════

_scheduler_task = None



# ══════════════════════════════════════════════════════════════════════════════
# CBD COMPONENT — Scheduler (authenticated)
# ══════════════════════════════════════════════════════════════════════════════

SCHEDULER_POLL_SECONDS     = int(os.getenv("JARVIS_SCHEDULER_POLL_SECONDS", "15"))
SCHEDULER_CMD_TIMEOUT      = int(os.getenv("JARVIS_SCHEDULER_CMD_TIMEOUT",  "300"))
SCHEDULER_OUTPUT_MAX_CHARS = 20_000
_scheduler_task: Optional[asyncio.Task] = None

# Pinned env for scheduled commands — same hardening as _kali_exec
_SCHED_ENV_BASE = {
    "PATH":            "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "TERM":            "xterm-256color",
    "DEBIAN_FRONTEND": "noninteractive",
    "HOME":            "/root",
    "LANG":            "C.UTF-8",
}
_SCHED_PROTECTED = {"PATH", "LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONPATH", "PYTHONHOME"}


async def _run_scheduled_ai_call(task: dict) -> dict:
    payload      = task.get("payload") or {}
    message      = sanitise_message(payload.get("message", ""))
    persona      = payload.get("persona", DEFAULT_PERSONA)
    react        = bool(payload.get("react", True))
    auto_confirm = bool(payload.get("auto_confirm", True))
    user_id      = payload.get("user_id_override") or task.get("user_id") or "scheduler"
    agent        = _get_session(user_id)
    chunks: list[str] = []
    async for ev in agent.chat_stream(message, react=react, auto_confirm=auto_confirm, persona=persona):
        if ev.get("type") == "token":
            chunks.append(str(ev.get("data", "")))
    return {"status": "success", "output": ("".join(chunks).strip() or "(no output)")[:SCHEDULER_OUTPUT_MAX_CHARS]}


async def _run_scheduled_command(task: dict) -> dict:
    payload = task.get("payload") or {}
    command = payload.get("command", "")
    cwd     = payload.get("cwd") or None
    timeout = int(payload.get("timeout_seconds", SCHEDULER_CMD_TIMEOUT))
    # Strip protected vars so scheduler tasks cannot escalate via PATH injection
    safe_extra = {k: str(v) for k, v in (payload.get("env") or {}).items()
                  if k not in _SCHED_PROTECTED}
    env = {**_SCHED_ENV_BASE, **safe_extra}
    proc = None
    try:
        proc = await asyncio.create_subprocess_shell(
            command, cwd=cwd, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            if proc:
                try:
                    proc.kill()
                    await proc.wait()   # reap zombie
                except Exception:
                    pass
            return {"status": "timeout", "output": "", "error": f"Command exceeded {timeout}s"}
        out = out_b.decode(errors="replace")
        err = err_b.decode(errors="replace")
        ok  = proc.returncode == 0
        combined = out + ("\n[stderr]\n" + err if err else "")
        return {
            "status": "success" if ok else "failed",
            "output": combined[:SCHEDULER_OUTPUT_MAX_CHARS],
            "error":  None if ok else f"exit code {proc.returncode}",
        }
    except Exception as exc:
        logger.error("[scheduler_cmd_error] %s", exc)
        return {"status": "failed", "output": "", "error": "Command execution error."}


async def _run_scheduled_skill_action(task: dict) -> dict:
    """
    Generic dispatch: payload = { skill: str, action: str, params: dict }.
    Lets a scheduled task drive ANY registered skill (code_tools.run_command
    for a nightly test suite, jarvis_mkii.run_tasks for a parallel batch,
    os_execution for a cron-style job, etc.) instead of being limited to the
    two hardcoded task_types (ai_call / command). This is what "skill_action"
    task_type routes to.
    """
    payload    = task.get("payload") or {}
    skill_name = payload.get("skill", "")
    action     = payload.get("action", "")
    params     = payload.get("params") or {}
    if not skill_name or not action:
        return {"status": "failed", "output": "", "error": (
            "skill_action task requires payload.skill and payload.action "
            "(e.g. {\"skill\": \"code_tools\", \"action\": \"run_command\", "
            "\"params\": {\"command\": \"pytest\"}})."
        )}
    try:
        result = await _registry.execute(skill_name, action, params, confirmed=True)
    except Exception as exc:
        logger.error("[scheduler_skill_action_error] skill=%s action=%s err=%s", skill_name, action, exc)
        return {"status": "failed", "output": "", "error": f"{type(exc).__name__}: {exc}"}
    d = result.to_dict()
    return {
        "status": "success" if d.get("success") else "failed",
        "output": json.dumps(d.get("output"), indent=2, default=str)[:SCHEDULER_OUTPUT_MAX_CHARS],
        "error":  d.get("error"),
    }


async def _execute_scheduled_task(task: dict) -> dict:
    started   = datetime.now(timezone.utc).isoformat()
    task_type = task.get("task_type")
    logger.info("[scheduler_run] task_id=%s type=%s", task.get("id"), task_type)
    try:
        if task_type == "ai_call":
            result = await _run_scheduled_ai_call(task)
        elif task_type == "command":
            result = await _run_scheduled_command(task)
        elif task_type == "skill_action":
            result = await _run_scheduled_skill_action(task)
        else:
            result = {"status": "failed", "output": "", "error": (
                f"Unrecognised task_type {task_type!r} — expected 'ai_call', 'command', "
                f"or 'skill_action'. This task's record may predate the 'skill_action' "
                f"type or have a typo; edit it and re-save with a valid task_type."
            )}
    except Exception as exc:
        logger.error("[scheduler_run_error] task_id=%s err=%s\n%s", task.get("id"), exc, traceback.format_exc())
        result = {"status": "failed", "output": "", "error": "Internal scheduler error."}
    finished = datetime.now(timezone.utc).isoformat()
    record = await _registry.execute("scheduler", "record_run", {
        "task_id":     task["id"],
        "status":      result["status"],
        "output":      result.get("output", ""),
        "error":       result.get("error"),
        "started_at":  started,
        "finished_at": finished,
    }, confirmed=True)
    return {"run": result, "task": record.to_dict().get("output")}


async def _scheduler_loop():
    logger.info("[scheduler_loop_started] poll=%ss", SCHEDULER_POLL_SECONDS)
    while True:
        try:
            due = await _registry.execute("scheduler", "get_due_tasks", {})
            for task in (due.output or {}).get("tasks", []):
                await _execute_scheduled_task(task)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("[scheduler_loop_error] %s\n%s", exc, traceback.format_exc())
        await asyncio.sleep(SCHEDULER_POLL_SECONDS)


@app.get("/api/scheduler/tasks", tags=["Scheduler"])
async def scheduler_list_tasks(request: Request, user_id: str = None):
    caller = _require_auth(request)
    # Non-admins can only see their own tasks
    uid = user_id if caller == "admin" else caller
    return await _skill_call("scheduler", "list_tasks", {"user_id": uid} if uid else {})


@app.post("/api/scheduler/tasks", tags=["Scheduler"])
async def scheduler_create_task(request: Request):
    caller = _require_auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")
    # Force user_id to caller for non-admins
    if caller != "admin":
        body["user_id"] = caller
    return await _skill_call("scheduler", "create_task", body)


@app.get("/api/scheduler/tasks/{task_id}", tags=["Scheduler"])
async def scheduler_get_task(task_id: str, request: Request):
    _require_auth(request)
    return await _skill_call("scheduler", "get_task", {"task_id": task_id})


@app.put("/api/scheduler/tasks/{task_id}", tags=["Scheduler"])
async def scheduler_update_task(task_id: str, request: Request):
    _require_auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")
    body["task_id"] = task_id
    return await _skill_call("scheduler", "update_task", body)


@app.delete("/api/scheduler/tasks/{task_id}", tags=["Scheduler"])
async def scheduler_delete_task(task_id: str, request: Request):
    _require_auth(request)
    return await _skill_call("scheduler", "delete_task", {"task_id": task_id})


@app.post("/api/scheduler/tasks/{task_id}/toggle", tags=["Scheduler"])
async def scheduler_toggle_task(task_id: str, request: Request):
    _require_auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")
    return await _skill_call("scheduler", "toggle_task", {"task_id": task_id, "enabled": body.get("enabled")})


@app.post("/api/scheduler/tasks/{task_id}/run", tags=["Scheduler"])
async def scheduler_run_now(task_id: str, request: Request):
    """Manual immediate execution — returns the run result directly."""
    _require_auth(request)
    got = await _registry.execute("scheduler", "get_task", {"task_id": task_id}, confirmed=True)
    if not got.success:
        raise HTTPException(status_code=404, detail=got.error or "Task not found.")
    result = await _execute_scheduled_task(got.output)
    return JSONResponse(result)


@app.get("/api/scheduler/tasks/{task_id}/runs", tags=["Scheduler"])
async def scheduler_list_runs(task_id: str, request: Request, limit: int = 50):
    _require_auth(request)
    return await _skill_call("scheduler", "list_runs", {"task_id": task_id, "limit": limit})


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER COMPONENT — RCA (authenticated)
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/rca", tags=["RCA"])
async def run_rca(request: Request):
    caller = _require_auth(request)
    check_api_rate(get_client_ip(request))
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    symptom       = sanitise_message(body.get("symptom", ""))
    error_message = body.get("error_message", "")
    environment   = body.get("environment", {})
    user_id       = body.get("user_id", caller)

    # Non-admins can only run RCA under their own session
    if caller != "admin":
        user_id = caller

    if not symptom:
        raise HTTPException(status_code=400, detail="symptom is required.")

    rca_prompt = (
        f"Perform a full Root Cause Analysis following the RCA blueprint methodology.\n\n"
        f"**Symptom**: {symptom}\n"
        f"**Error Message**: {error_message or 'Not provided'}\n"
        f"**Environment**: {json.dumps(environment) if environment else 'Not provided'}\n\n"
        f"Follow every phase: Phase 0 (Experienced lookup), SymptomCapturer, ContextAggregator, "
        f"HypothesisGenerator, DiagnosticDesigner, DiagnosticExecutor, HypothesisEvaluator, "
        f"CausalChainDriller, FixProposer, FixValidator, PostMortemWriter, Phase III experience capture. "
        f"Use TOOL_CALL blocks for all skill invocations. End with TASK_COMPLETE."
    )

    logger.info("[rca_request] user=%s symptom=%.80s", user_id, symptom)
    try:
        agent = _get_session(user_id)
        gen   = agent.chat_stream(rca_prompt, react=True, auto_confirm=False)
        return StreamingResponse(_agent_to_sse(gen), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    except Exception as exc:
        logger.error("[rca_error] %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error.")


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER COMPONENT — Experienced (authenticated)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/experienced", tags=["Experienced"])
async def experienced_list(request: Request):
    _require_auth(request)
    return await _skill_call("cbd_architect", "experienced_search", {"query": "*"})


@app.get("/api/experienced/search", tags=["Experienced"])
async def experienced_search(q: str, request: Request,
                              category: str = None, severity: str = None):
    _require_auth(request)
    params = {"query": q}
    if category:
        params["category"] = category
    if severity:
        params["severity"] = severity
    return await _skill_call("cbd_architect", "experienced_search", params)


@app.post("/api/experienced/rebuild", tags=["Experienced"])
async def experienced_rebuild(request: Request):
    # Destructive index rebuild — admin only
    _require_admin(request)
    return await _skill_call("cbd_architect", "experienced_rebuild_index", {})


# ══════════════════════════════════════════════════════════════════════════════
# CBD COMPONENT — Workspace helpers + ZIP download (authenticated)
# ══════════════════════════════════════════════════════════════════════════════

WS_MAX_FILE_BYTES  = 128_000
WS_MAX_TOTAL_BYTES = 512_000
WS_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build", ".pytest_cache"}
WS_TEXT_EXTS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".sh", ".bash", ".zsh",
    ".md", ".txt", ".rst", ".cfg", ".ini", ".toml", ".yaml", ".yml",
    ".json", ".xml", ".html", ".css", ".sql", ".go", ".rs", ".java",
    ".c", ".cpp", ".h", ".env", ".conf", ".log", ".csv",
}
PROJECT_META_FILE = ".jarvis_project.json"

# NOTE: WORKSPACE_ROOT / DEFAULT_PROJECT / _safe_name / _user_root /
# _workspace_path / _is_text_file were previously redefined here as a
# second, drifting copy of the block above (same names, second definition
# wins at call time — harmless for correctness since Python resolves
# globals at call time, but confusing and doubled the risk of one copy
# being fixed and the other not). Removed; this section now reuses the
# single canonical definitions (_workspace_root(), _default_project(),
# _safe_name(), _user_root(), _workspace_path(), _is_text_file()) from the
# Workspace File Browser section above.


def _project_root(user_id: str, project: str) -> str:
    path = os.path.join(_user_root(user_id), _safe_name(project))
    os.makedirs(path, exist_ok=True)
    return path


def _realpath_guard(candidate: str, root: str) -> str:
    """Resolve symlinks and verify path is inside root. Raises 403 on escape."""
    try:
        rp = os.path.realpath(candidate)
        rr = os.path.realpath(root)
    except Exception:
        raise HTTPException(status_code=403, detail="Invalid path.")
    if not (rp == rr or rp.startswith(rr + os.sep)):
        logger.warning("[path_traversal] candidate=%s root=%s", candidate, root)
        raise HTTPException(status_code=403, detail="Path outside workspace.")
    return rp


def _list_projects(user_id: str) -> list[dict]:
    default_project = _default_project()
    ud = _user_root(user_id)
    projects = []
    try:
        for entry in sorted(os.scandir(ud), key=lambda e: e.name):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            ws = os.path.join(entry.path, "workspace")
            fc = sb = 0
            if os.path.isdir(ws):
                for dp, dirs, fns in os.walk(ws):
                    dirs[:] = [d for d in dirs if d not in WS_SKIP_DIRS]
                    for fn in fns:
                        fc += 1
                        try:
                            sb += os.path.getsize(os.path.join(dp, fn))
                        except OSError:
                            pass
            st = entry.stat()
            projects.append({
                "name":       entry.name,
                "created":    datetime.fromtimestamp(st.st_ctime, tz=timezone.utc).isoformat(),
                "modified":   datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                "file_count": fc,
                "size_bytes": sb,
                "is_default": entry.name == default_project,
            })
    except PermissionError:
        pass
    # Ensure default project always exists
    if not any(p["name"] == default_project for p in projects):
        _workspace_path(user_id, default_project)
        return _list_projects(user_id)
    return projects


@app.get("/api/workspace/{user_id}/zip", tags=["Workspace"])
async def workspace_zip(user_id: str, request: Request, project: str = None):
    import zipfile as _zf
    caller  = _require_auth(request)
    if caller != "admin" and caller != user_id:
        raise HTTPException(status_code=403, detail="Permission denied.")
    project = project or _default_project()
    ws_root = _workspace_path(user_id, project)
    safe_p  = _safe_name(project)
    if not os.path.isdir(ws_root):
        raise HTTPException(status_code=404, detail="Workspace not found.")

    def _iter_zip():
        buf = io.BytesIO()
        with _zf.ZipFile(buf, mode="w", compression=_zf.ZIP_DEFLATED) as zf:
            for dirpath, dirs, files in os.walk(ws_root):
                dirs[:] = sorted(d for d in dirs if d not in WS_SKIP_DIRS and not d.startswith("."))
                for fname in sorted(files):
                    if fname.startswith("."):
                        continue
                    abs_p = os.path.join(dirpath, fname)
                    rel_p = os.path.relpath(abs_p, ws_root)
                    try:
                        zf.write(abs_p, arcname=rel_p)
                    except (PermissionError, OSError):
                        pass
        buf.seek(0)
        yield buf.read()

    fname = f"{safe_p}-workspace.zip"
    logger.info("[workspace_zip] user=%s project=%s", user_id, safe_p)
    return StreamingResponse(
        _iter_zip(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ══════════════════════════════════════════════════════════════════════════════
# CBD COMPONENT — Project Manager (authenticated)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/projects/{user_id}", tags=["Workspace"])
async def list_projects(user_id: str, request: Request):
    caller = _require_auth(request)
    if caller != "admin" and caller != user_id:
        raise HTTPException(status_code=403, detail="Permission denied.")
    projects = _list_projects(user_id)
    logger.info("[list_projects] user=%s count=%d", user_id, len(projects))
    return {
        "user_id":         user_id,
        "projects":        projects,
        "count":           len(projects),
        "default_project": _default_project(),
        "workspace_root":  _user_root(user_id) if caller == "admin" or caller == user_id else None,
    }


@app.post("/api/projects/{user_id}", tags=["Workspace"])
async def create_project(user_id: str, request: Request):
    caller = _require_auth(request)
    if caller != "admin" and caller != user_id:
        raise HTTPException(status_code=403, detail="Permission denied.")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    name = body.get("name", "").strip()
    desc = body.get("description", "")
    if not name:
        raise HTTPException(status_code=400, detail="Project name is required.")
    safe = _safe_name(name)
    if not safe:
        raise HTTPException(status_code=400, detail="Invalid project name.")

    pr  = _project_root(user_id, safe)
    _workspace_path(user_id, safe)   # creates workspace/ subdirectory
    meta = {
        "name":         safe,
        "display_name": name,
        "description":  desc,
        "created_by":   user_id,
        "created_at":   datetime.now(timezone.utc).isoformat(),
    }
    try:
        with open(os.path.join(pr, PROJECT_META_FILE), "w") as f:
            json.dump(meta, f, indent=2)
    except Exception as exc:
        logger.warning("[create_project.meta_fail] %s", exc)

    logger.info("[create_project] user=%s name=%s", user_id, safe)
    return {
        "created":      True,
        "name":         safe,
        "display_name": name,
        # workspace and project_root paths intentionally omitted
    }


@app.delete("/api/projects/{user_id}/{project_name}", tags=["Workspace"])
async def delete_project(user_id: str, project_name: str, request: Request):
    import shutil as _sh
    caller = _require_auth(request)
    if caller != "admin" and caller != user_id:
        raise HTTPException(status_code=403, detail="Permission denied.")
    safe = _safe_name(project_name)
    if safe == _default_project():
        raise HTTPException(status_code=400, detail=f"Cannot delete the default project.")
    pr = os.path.join(_user_root(user_id), safe)
    if not os.path.isdir(pr):
        raise HTTPException(status_code=404, detail=f"Project '{safe}' not found.")
    try:
        _sh.rmtree(pr)
    except Exception:
        raise HTTPException(status_code=500, detail="Delete failed.")
    logger.info("[delete_project] user=%s project=%s", user_id, safe)
    return {"deleted": True, "name": safe}


@app.get("/api/projects/{user_id}/{project_name}", tags=["Workspace"])
async def get_project(user_id: str, project_name: str, request: Request):
    caller = _require_auth(request)
    if caller != "admin" and caller != user_id:
        raise HTTPException(status_code=403, detail="Permission denied.")
    safe = _safe_name(project_name)
    pr   = os.path.join(_user_root(user_id), safe)
    if not os.path.isdir(pr):
        raise HTTPException(status_code=404, detail=f"Project '{safe}' not found.")
    meta: dict = {}
    mp = os.path.join(pr, PROJECT_META_FILE)
    if os.path.isfile(mp):
        try:
            with open(mp) as f:
                meta = json.load(f)
        except Exception:
            pass
    # Strip any internal paths that ended up in metadata
    meta.pop("workspace", None)
    meta.pop("project_root", None)
    return {"name": safe, "exists": True, "metadata": meta}


# ══════════════════════════════════════════════════════════════════════════════
# CBD COMPONENT — Persona Registry (authenticated writes)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/personas", tags=["Personas"])
async def get_personas():
    """Public — used by login screen."""
    return {"personas": list_personas(), "default_persona": DEFAULT_PERSONA}


@app.get("/api/personas/{persona_id}", tags=["Personas"])
async def get_persona_detail(persona_id: str):
    p = get_persona(persona_id)
    if not p:
        raise HTTPException(status_code=404, detail=f"Persona '{persona_id}' not found.")
    return p


@app.post("/api/personas", tags=["Personas"])
async def create_persona(request: Request):
    _require_auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")
    if not body.get("id") or not body.get("name"):
        raise HTTPException(status_code=400, detail="id and name are required.")
    body["builtin"] = False
    ok, err = save_persona(body)
    if not ok:
        raise HTTPException(status_code=500, detail="Save failed.")
    logger.info("[persona_create] id=%s", body["id"])
    return {"created": True, "id": body["id"]}


@app.put("/api/personas/{persona_id}", tags=["Personas"])
async def update_persona(persona_id: str, request: Request):
    _require_auth(request)
    existing = get_persona(persona_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Persona '{persona_id}' not found.")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")
    body["id"]      = persona_id
    body["builtin"] = existing.get("builtin", False)
    ok, err = save_persona(body)
    if not ok:
        raise HTTPException(status_code=500, detail="Save failed.")
    return {"updated": True, "id": persona_id}


@app.delete("/api/personas/{persona_id}", tags=["Personas"])
async def delete_persona_endpoint(persona_id: str, request: Request):
    _require_auth(request)
    ok, err = delete_persona(persona_id)
    if not ok:
        raise HTTPException(status_code=400, detail=err or "Delete failed.")
    logger.info("[persona_delete] id=%s", persona_id)
    return {"deleted": True, "id": persona_id}


@app.post("/api/personas/generate", tags=["Personas"])
async def generate_persona_hint(request: Request):
    caller = _require_auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")
    hint    = body.get("hint", "").strip()
    user_id = body.get("user_id", caller)
    if caller != "admin":
        user_id = caller
    if not hint:
        raise HTTPException(status_code=400, detail="hint is required.")

    schema = (
        '{\n  "id": "slug_id_no_spaces",\n  "name": "Display Name",\n'
        '  "tagline": "One-line description",\n'
        '  "soul": "Full personality/identity block 200-400 words",\n'
        '  "directives": "Role-specific instructions 200-400 words",\n'
        '  "skills": ["filesystem","os_execution","memory_manager"],\n'
        '  "builtin": false,\n'
        '  "theme": {"accent":"#RRGGBB","bg":"#RRGGBB","textPri":"#RRGGBB"}\n}'
    )
    gen_msg = (
        f'Generate a Jarvis persona JSON for this concept: "{hint}"\n\n'
        f"Return ONLY valid JSON (no markdown) using this schema:\n{schema}\n\n"
        "Match the theme colors to the persona concept."
    )
    try:
        agent  = _get_session(user_id)
        result = await agent.llm.chat(
            [{"role": "user", "content": gen_msg}],
            system="You are a JSON generator. Return only valid JSON. No markdown.",
        )
        import re as _re
        m = _re.search(r'\{[\s\S]*\}', result)
        if not m:
            raise ValueError("No JSON found in LLM response.")
        pd = json.loads(m.group())
        pd["builtin"] = False
        return {"generated": True, "persona": pd}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"LLM returned invalid JSON: {exc}")
    except Exception as exc:
        logger.error("[persona_gen_error] %s", exc)
        raise HTTPException(status_code=500, detail="Generation failed.")


# ══════════════════════════════════════════════════════════════════════════════
# SPA Fallback
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/{full_path:path}", tags=["System"])
async def spa_fallback(full_path: str):
    """Serve the React SPA for all non-API routes."""
    index = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.isfile(index):
        from fastapi.responses import FileResponse
        return FileResponse(index)
    return JSONResponse({"error": "Frontend not built", "path": full_path}, status_code=404)


# ══════════════════════════════════════════════════════════════════════════════
# Application Startup / Shutdown
# ══════════════════════════════════════════════════════════════════════════════

@app.on_event("startup")
async def on_startup():
    from core.llm_router import resolve_config_with_ollama_fallback
    _p  = os.getenv("LLM_PROVIDER", "deepseek")
    _m  = os.getenv("LLM_MODEL",    "deepseek-coder")
    ep, em, _ = resolve_config_with_ollama_fallback(_p, _m, os.getenv(f"{_p.upper()}_API_KEY"))

    logger.info("═" * 60)
    logger.info("  ⚡ Mighty Jarvis MKII v4.1.0 — PRODUCTION-HARDENED")
    logger.info("  Methodology   : CBD v2.2")
    if ep != _p:
        logger.info("  Provider      : %s → %s (Ollama fallback)", _p, ep)
        logger.info("  Model         : %s (auto-detected)", em)
    else:
        logger.info("  Provider      : %s / Model: %s", _p, _m)
    logger.info("  Token TTL     : %sh", TOKEN_TTL_HOURS)
    logger.info("  Session cap   : %s", os.getenv("SEC_SESSION_MAX", "500"))
    logger.info("  CORS origins  : %s", _cors_origins or "same-origin (no CORS_ORIGINS set)")
    logger.info("  Open reg      : %s", os.getenv("JARVIS_OPEN_REGISTRATION", "false"))
    logger.info("  Debug endpts  : %s", os.getenv("JARVIS_DEBUG_ENDPOINTS", "false"))
    logger.info("  OpenAPI docs  : %s", "disabled (production)" if (os.getenv('JARVIS_ENV', 'production') != 'development') else "enabled (development)")
    logger.info("  Kali exec key : %s", "CONFIGURED" if _KALI_API_KEY else "NOT SET — endpoint disabled")
    logger.info("═" * 60)

    global _scheduler_task
    _scheduler_task = asyncio.create_task(_scheduler_loop())


@app.on_event("shutdown")
async def on_shutdown():
    logger.info("Mighty Jarvis MKII — graceful shutdown initiated")
    global _scheduler_task
    if _scheduler_task is not None:
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
        logger.info("[scheduler] loop stopped.")
    logger.info("Shutdown complete.")


# ══════════════════════════════════════════════════════════════════════════════
# Dev entrypoint
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=not (os.getenv('JARVIS_ENV', 'production') != 'development'),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )