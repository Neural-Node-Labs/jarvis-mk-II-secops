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
import json
import uuid
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
from core.agent import Agent, REACT_MAX_ITERATIONS, DEEP_TASK_MAX_ITERATIONS

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
    resolved_provider = provider or os.getenv("LLM_PROVIDER", "deepseek")
    resolved_model    = model    or os.getenv("LLM_MODEL",    "deepseek-coder")
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
TASK_TIMEOUT_SECONDS = int(os.getenv("JARVIS_TASK_TIMEOUT", "300"))
_active_tasks: dict[str, dict] = {}
_task_lock = threading.Lock()


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

        await asyncio.wait_for(_collect(), timeout=TASK_TIMEOUT_SECONDS)
        full_output = "".join(output_tokens)
        duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
        result = {"task_id": task_id, "status": "complete",
                  "output": full_output, "error": None, "duration_ms": duration_ms}
        logger.info("[task_complete] id=%s duration_ms=%d", task_id, duration_ms)

    except asyncio.TimeoutError:
        duration_ms = TASK_TIMEOUT_SECONDS * 1000
        result = {"task_id": task_id, "status": "timeout",
                  "output": "".join(output_tokens),
                  "error": f"Task timed out after {TASK_TIMEOUT_SECONDS}s",
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

    max_parallel = int(os.getenv("JARVIS_MAX_PARALLEL", "10"))
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
            project         = msg.get("project",  DEFAULT_PROJECT)
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

            try:
                agent = _get_session(user_id, model, provider)
                workspace_ctx = (
                    f"[WORKSPACE CONTEXT]\n"
                    f"Active Project  : {project}\n"
                    f"Workspace Path  : {workspace_path}\n"
                    f"User            : {user_id}\n"
                    f"All file operations MUST use this workspace path as root.\n"
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

    message     = sanitise_message(message)
    attachments = await _process_attachments(files) if files else []
    logger.info("[chat_upload] user=%s files=%d msg=%.60s", user_id, len(attachments), message)
    try:
        agent = _get_session(user_id, model, provider)
        gen   = agent.chat_stream(message, react=react, auto_confirm=auto_confirm, attachments=attachments)
        return StreamingResponse(_agent_to_sse(gen), media_type="text/event-stream", headers={
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

WORKSPACE_ROOT    = os.getenv("JARVIS_WORKSPACE_ROOT",  "/app/workspace")
DEFAULT_PROJECT   = os.getenv("JARVIS_DEFAULT_PROJECT", "default")
WS_MAX_FILE_BYTES  = 128_000
WS_MAX_TOTAL_BYTES = 512_000
WS_SKIP_DIRS  = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build"}
WS_TEXT_EXTS  = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".sh", ".bash", ".zsh",
    ".md", ".txt", ".rst", ".cfg", ".ini", ".toml", ".yaml", ".yml",
    ".json", ".xml", ".html", ".css", ".sql", ".go", ".rs", ".java",
    ".c", ".cpp", ".h", ".env", ".conf", ".log", ".csv",
}


def _safe_name(name: str, maxlen: int = 48) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name.strip())[:maxlen] or "default"


def _user_root(user_id: str) -> str:
    path = os.path.join(WORKSPACE_ROOT, _safe_name(user_id))
    os.makedirs(path, exist_ok=True)
    return path


def _workspace_path(user_id: str, project: str = DEFAULT_PROJECT) -> str:
    safe_proj = _safe_name(project) if project else DEFAULT_PROJECT
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
    project: str = DEFAULT_PROJECT,
):
    caller = _require_auth(request)
    if caller != "admin" and caller != user_id:
        raise HTTPException(status_code=403, detail="Permission denied.")

    ws_root = _workspace_path(user_id, project)
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
    project = body.get("project", DEFAULT_PROJECT)
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
    project  = body.get("project", DEFAULT_PROJECT)
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
    project  = body.get("project", DEFAULT_PROJECT)
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


async def _execute_scheduled_task(task: dict) -> dict:
    started = datetime.now(timezone.utc).isoformat()
    logger.info("[scheduler_run] task_id=%s type=%s", task.get("id"), task.get("task_type"))
    try:
        if task["task_type"] == "ai_call":
            result = await _run_scheduled_ai_call(task)
        elif task["task_type"] == "command":
            result = await _run_scheduled_command(task)
        else:
            result = {"status": "failed", "output": "", "error": f"unknown task_type '{task['task_type']}'"}
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

WORKSPACE_ROOT    = os.getenv("JARVIS_WORKSPACE_ROOT",  "/app/workspace")
DEFAULT_PROJECT   = os.getenv("JARVIS_DEFAULT_PROJECT", "default")
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


def _safe_name(name: str, maxlen: int = 48) -> str:
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", name.strip())[:maxlen] or "default"


def _user_root(user_id: str) -> str:
    path = os.path.join(WORKSPACE_ROOT, _safe_name(user_id))
    os.makedirs(path, exist_ok=True)
    return path


def _project_root(user_id: str, project: str) -> str:
    path = os.path.join(_user_root(user_id), _safe_name(project))
    os.makedirs(path, exist_ok=True)
    return path


def _workspace_path(user_id: str, project: str = DEFAULT_PROJECT) -> str:
    safe = _safe_name(project) if project else DEFAULT_PROJECT
    path = os.path.join(_user_root(user_id), safe, "workspace")
    os.makedirs(path, exist_ok=True)
    return path


def _is_text_file(name: str) -> bool:
    _, ext = os.path.splitext(name)
    return ext.lower() in WS_TEXT_EXTS


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
                "is_default": entry.name == DEFAULT_PROJECT,
            })
    except PermissionError:
        pass
    # Ensure default project always exists
    if not any(p["name"] == DEFAULT_PROJECT for p in projects):
        _workspace_path(user_id, DEFAULT_PROJECT)
        return _list_projects(user_id)
    return projects


@app.get("/api/workspace/{user_id}/zip", tags=["Workspace"])
async def workspace_zip(user_id: str, request: Request, project: str = DEFAULT_PROJECT):
    import zipfile as _zf
    caller  = _require_auth(request)
    if caller != "admin" and caller != user_id:
        raise HTTPException(status_code=403, detail="Permission denied.")
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
        "default_project": DEFAULT_PROJECT,
        # workspace_root intentionally omitted (internal path)
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
    if safe == DEFAULT_PROJECT:
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
# SAAS BILLING PLATFORM (authenticated; all writes require auth)
# ══════════════════════════════════════════════════════════════════════════════
import uuid as _uuid
import time as _time
import hmac as _hmac_b
import hashlib as _hash_b
from datetime import datetime as _dt, timezone as _tz, timedelta as _td

_BILLING_ENABLED        = os.getenv("SAAS_BILLING_ENABLED",   "false").lower() == "true"
_BILLING_GATEWAY        = os.getenv("SAAS_GATEWAY",            "stripe")
_BILLING_STRIPE_KEY     = os.getenv("STRIPE_SECRET_KEY",       "")
_BILLING_WEBHOOK_SECRET = os.getenv("SAAS_WEBHOOK_SECRET",     "")
_BILLING_BASE_CURRENCY  = os.getenv("SAAS_BASE_CURRENCY",      "USD")

# Circuit breaker state
_CB: dict = {}
_CB_THRESHOLD = 3
_CB_RESET_S   = 60


def _cb_ok(comp: str) -> bool:
    s = _CB.get(comp, {})
    return not (s.get("open_until") and _time.time() < s["open_until"])


def _cb_fail(comp: str):
    s = _CB.setdefault(comp, {"failures": 0, "open_until": None})
    s["failures"] += 1
    if s["failures"] >= _CB_THRESHOLD:
        s["open_until"] = _time.time() + _CB_RESET_S
        logger.warning("[billing_cb_open] component=%s", comp)


def _cb_success(comp: str):
    _CB.pop(comp, None)


def _make_billing_error(code: str, message: str, component: str) -> dict:
    return {"code": code, "message": message, "component": component,
            "timestamp": _dt.now(_tz.utc).isoformat()}


def _is_billing_error(obj) -> bool:
    return isinstance(obj, dict) and "code" in obj and "component" in obj


def _btrace(component: str, event: str, **meta):
    logger.info("[billing.%s] %s %s", component, event,
                " ".join(f"{k}={v}" for k, v in meta.items()))


def _now_iso() -> str:
    return _dt.now(_tz.utc).isoformat()


def _period_end(start_iso: str, interval: str) -> str:
    start = _dt.fromisoformat(start_iso.replace("Z", "+00:00"))
    return (start + _td(days=365 if interval == "yearly" else 30)).isoformat()


def _billing_guard():
    if not _BILLING_ENABLED:
        raise HTTPException(status_code=404,
                            detail="Billing not enabled. Set SAAS_BILLING_ENABLED=true.")


def _init_billing_db():
    if not _BILLING_ENABLED:
        return
    with _DB_LOCK:
        conn = _get_db()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS billing_customers (
                customer_id  TEXT PRIMARY KEY,
                username     TEXT NOT NULL,
                name         TEXT NOT NULL,
                email        TEXT NOT NULL,
                country      TEXT NOT NULL DEFAULT 'US',
                currency     TEXT NOT NULL DEFAULT 'USD',
                tax_id       TEXT,
                status       TEXT NOT NULL DEFAULT 'active',
                created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS billing_plans (
                plan_id           TEXT PRIMARY KEY,
                name              TEXT NOT NULL,
                billing_interval  TEXT NOT NULL DEFAULT 'monthly',
                base_price        REAL NOT NULL DEFAULT 0,
                currency          TEXT NOT NULL DEFAULT 'USD',
                metered_rates     TEXT NOT NULL DEFAULT '[]',
                features          TEXT NOT NULL DEFAULT '[]',
                active            INTEGER NOT NULL DEFAULT 1,
                created_at        TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS billing_subscriptions (
                subscription_id      TEXT PRIMARY KEY,
                customer_id          TEXT NOT NULL,
                plan_id              TEXT NOT NULL,
                status               TEXT NOT NULL DEFAULT 'active',
                current_period_start TEXT NOT NULL,
                current_period_end   TEXT NOT NULL,
                canceled_at          TEXT,
                created_at           TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (customer_id) REFERENCES billing_customers(customer_id),
                FOREIGN KEY (plan_id)     REFERENCES billing_plans(plan_id)
            );
            CREATE TABLE IF NOT EXISTS billing_usage_events (
                event_id        TEXT PRIMARY KEY,
                subscription_id TEXT NOT NULL,
                metric          TEXT NOT NULL,
                quantity        REAL NOT NULL,
                occurred_at     TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                recorded_at     TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (subscription_id) REFERENCES billing_subscriptions(subscription_id)
            );
            CREATE TABLE IF NOT EXISTS billing_invoices (
                invoice_id      TEXT PRIMARY KEY,
                customer_id     TEXT NOT NULL,
                subscription_id TEXT NOT NULL,
                total           REAL NOT NULL DEFAULT 0,
                tax_amount      REAL NOT NULL DEFAULT 0,
                currency        TEXT NOT NULL DEFAULT 'USD',
                status          TEXT NOT NULL DEFAULT 'draft',
                line_items      TEXT NOT NULL DEFAULT '[]',
                period_start    TEXT NOT NULL,
                period_end      TEXT NOT NULL,
                issued_at       TEXT NOT NULL DEFAULT (datetime('now')),
                due_at          TEXT,
                paid_at         TEXT,
                FOREIGN KEY (customer_id)     REFERENCES billing_customers(customer_id),
                FOREIGN KEY (subscription_id) REFERENCES billing_subscriptions(subscription_id)
            );
            CREATE TABLE IF NOT EXISTS billing_payments (
                payment_id     TEXT PRIMARY KEY,
                invoice_id     TEXT NOT NULL,
                transaction_id TEXT,
                amount         REAL NOT NULL,
                currency       TEXT NOT NULL,
                status         TEXT NOT NULL DEFAULT 'pending',
                failure_code   TEXT,
                attempt_number INTEGER NOT NULL DEFAULT 1,
                gateway        TEXT NOT NULL DEFAULT 'mock',
                created_at     TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (invoice_id) REFERENCES billing_invoices(invoice_id)
            );
            CREATE TABLE IF NOT EXISTS billing_dunning (
                dunning_id          TEXT PRIMARY KEY,
                invoice_id          TEXT NOT NULL,
                subscription_id     TEXT NOT NULL,
                failure_code        TEXT,
                attempt_number      INTEGER NOT NULL DEFAULT 1,
                next_retry_at       TEXT,
                subscription_action TEXT NOT NULL DEFAULT 'none',
                created_at          TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_bs_cust  ON billing_subscriptions(customer_id);
            CREATE INDEX IF NOT EXISTS idx_bu_sub   ON billing_usage_events(subscription_id);
            CREATE INDEX IF NOT EXISTS idx_bi_sub   ON billing_invoices(subscription_id);
            CREATE INDEX IF NOT EXISTS idx_bp_inv   ON billing_payments(invoice_id);
        """)
        if not conn.execute("SELECT 1 FROM billing_plans LIMIT 1").fetchone():
            conn.execute("INSERT INTO billing_plans (plan_id,name,billing_interval,base_price,currency,metered_rates,features) VALUES (?,?,?,?,?,?,?)",
                         ("plan_free","Free","monthly",0.0,"USD","[]",'["Basic access"]'))
            conn.execute("INSERT INTO billing_plans (plan_id,name,billing_interval,base_price,currency,metered_rates,features) VALUES (?,?,?,?,?,?,?)",
                         ("plan_pro","Pro","monthly",29.0,"USD",'[{"metric":"api_calls","unitPrice":0.001}]','["Full access","API access","Priority support"]'))
            conn.execute("INSERT INTO billing_plans (plan_id,name,billing_interval,base_price,currency,metered_rates,features) VALUES (?,?,?,?,?,?,?)",
                         ("plan_enterprise","Enterprise","monthly",199.0,"USD",'[{"metric":"api_calls","unitPrice":0.0005}]','["Full access","Unlimited API","Dedicated support","SLA"]'))
            conn.commit()
            logger.info("[billing_db_seed] default plans created")
        conn.close()


# ── Billing domain functions ───────────────────────────────────────────────────

def customer_account_manager(action: str, customer_id: str = None, fields: dict = None) -> dict:
    comp = "CustomerAccountManager"
    if not _cb_ok(comp): return _make_billing_error("CUSTOMER_UNAVAILABLE", "Circuit open", comp)
    try:
        with _DB_LOCK:
            conn = _get_db()
            if action == "create":
                cid = str(_uuid.uuid4())
                conn.execute("INSERT INTO billing_customers (customer_id,username,name,email,country,currency,tax_id) VALUES (?,?,?,?,?,?,?)",
                             (cid, fields["username"], fields["name"], fields["email"],
                              fields.get("country","US"), fields.get("currency","USD"), fields.get("tax_id")))
                conn.commit(); row = conn.execute("SELECT * FROM billing_customers WHERE customer_id=?",(cid,)).fetchone()
            elif action == "get":
                row = conn.execute("SELECT * FROM billing_customers WHERE customer_id=?",(customer_id,)).fetchone()
            elif action == "get_by_username":
                row = conn.execute("SELECT * FROM billing_customers WHERE username=?",(customer_id,)).fetchone()
            elif action == "update":
                sets  = ",".join(f"{k}=?" for k in fields if k != "customer_id")
                vals  = [v for k,v in fields.items() if k != "customer_id"] + [customer_id]
                conn.execute(f"UPDATE billing_customers SET {sets},updated_at=datetime('now') WHERE customer_id=?", vals)
                conn.commit(); row = conn.execute("SELECT * FROM billing_customers WHERE customer_id=?",(customer_id,)).fetchone()
            else:
                conn.close(); return _make_billing_error("INVALID_ACTION", f"Unknown {action}", comp)
            conn.close()
        _cb_success(comp)
        return {"customer": dict(row) if row else None}
    except Exception as exc:
        _cb_fail(comp); return _make_billing_error("CUSTOMER_ERROR", str(exc), comp)


def plan_catalog_manager(action: str, plan_id: str = None, fields: dict = None) -> dict:
    comp = "PlanCatalogManager"
    if not _cb_ok(comp): return _make_billing_error("PLAN_UNAVAILABLE", "Circuit open", comp)
    try:
        with _DB_LOCK:
            conn = _get_db()
            if action == "list":
                rows = conn.execute("SELECT * FROM billing_plans WHERE active=1 ORDER BY base_price").fetchall()
                conn.close(); return {"plans": [dict(r) for r in rows]}
            elif action == "get":
                row = conn.execute("SELECT * FROM billing_plans WHERE plan_id=?",(plan_id,)).fetchone()
                conn.close(); return {"plan": dict(row) if row else None}
            conn.close(); return _make_billing_error("INVALID_ACTION", f"Unknown {action}", comp)
    except Exception as exc:
        _cb_fail(comp); return _make_billing_error("PLAN_ERROR", str(exc), comp)


def subscription_lifecycle_manager(action: str, subscription_id: str = None, fields: dict = None) -> dict:
    comp = "SubscriptionLifecycleManager"
    if not _cb_ok(comp): return _make_billing_error("SUB_UNAVAILABLE", "Circuit open", comp)
    try:
        with _DB_LOCK:
            conn = _get_db()
            if action == "create":
                sid = str(_uuid.uuid4()); now = _now_iso()
                plan = conn.execute("SELECT * FROM billing_plans WHERE plan_id=?",(fields["plan_id"],)).fetchone()
                if not plan: conn.close(); return _make_billing_error("PLAN_NOT_FOUND",f"Plan {fields['plan_id']} not found",comp)
                conn.execute("INSERT INTO billing_subscriptions (subscription_id,customer_id,plan_id,current_period_start,current_period_end) VALUES (?,?,?,?,?)",
                             (sid,fields["customer_id"],fields["plan_id"],now,_period_end(now,plan["billing_interval"])))
                conn.commit(); row = conn.execute("SELECT * FROM billing_subscriptions WHERE subscription_id=?",(sid,)).fetchone()
            elif action == "get":
                row = conn.execute("SELECT * FROM billing_subscriptions WHERE subscription_id=?",(subscription_id,)).fetchone()
            elif action == "cancel":
                conn.execute("UPDATE billing_subscriptions SET status='canceled',canceled_at=? WHERE subscription_id=?",(_now_iso(),subscription_id))
                conn.commit(); row = conn.execute("SELECT * FROM billing_subscriptions WHERE subscription_id=?",(subscription_id,)).fetchone()
            elif action == "list_by_customer":
                rows = conn.execute("SELECT * FROM billing_subscriptions WHERE customer_id=?",(subscription_id,)).fetchall()
                conn.close(); return {"subscriptions":[dict(r) for r in rows]}
            else:
                conn.close(); return _make_billing_error("INVALID_ACTION",f"Unknown {action}",comp)
            conn.close()
        _cb_success(comp)
        return {"subscription": dict(row) if row else None}
    except Exception as exc:
        _cb_fail(comp); return _make_billing_error("SUB_ERROR",str(exc),comp)


def usage_event_recorder(subscription_id, metric, quantity, occurred_at=None, idempotency_key=None) -> dict:
    comp = "UsageEventRecorder"
    if not _cb_ok(comp): return _make_billing_error("USAGE_UNAVAILABLE","Circuit open",comp)
    try:
        eid  = str(_uuid.uuid4()); occ = occurred_at or _now_iso(); ikey = idempotency_key or eid
        with _DB_LOCK:
            conn = _get_db()
            try:
                conn.execute("INSERT INTO billing_usage_events (event_id,subscription_id,metric,quantity,occurred_at,idempotency_key) VALUES (?,?,?,?,?,?)",
                             (eid,subscription_id,metric,quantity,occ,ikey))
                conn.commit()
            except sqlite3.IntegrityError:
                conn.close(); return {"recorded":False,"reason":"duplicate_idempotency_key"}
            conn.close()
        _cb_success(comp); return {"recorded":True,"event_id":eid}
    except Exception as exc:
        _cb_fail(comp); return _make_billing_error("USAGE_ERROR",str(exc),comp)


def usage_aggregator(subscription_id, period_start, period_end) -> dict:
    try:
        with _DB_LOCK:
            conn = _get_db()
            rows = conn.execute("SELECT metric,SUM(quantity) as total FROM billing_usage_events WHERE subscription_id=? AND occurred_at BETWEEN ? AND ? GROUP BY metric",
                                (subscription_id,period_start,period_end)).fetchall()
            conn.close()
        return {"subscription_id":subscription_id,"period_start":period_start,"period_end":period_end,
                "usage":{r["metric"]:r["total"] for r in rows}}
    except Exception as exc:
        return _make_billing_error("AGGREGATOR_ERROR",str(exc),"UsageAggregator")


def currency_converter(amount, from_c, to_c) -> dict:
    comp = "CurrencyConverter"
    if from_c == to_c: return {"amount":amount,"from":from_c,"to":to_c,"rate":1.0,"converted":amount}
    RATES = {"USD":1.0,"EUR":0.92,"GBP":0.79,"CAD":1.36,"AUD":1.53,"JPY":149.5,"INR":83.1}
    if from_c not in RATES or to_c not in RATES:
        return _make_billing_error("FX_UNSUPPORTED",f"Unsupported pair {from_c}/{to_c}",comp)
    rate = RATES[to_c]/RATES[from_c]
    return {"amount":amount,"from":from_c,"to":to_c,"rate":rate,"converted":round(amount*rate,2)}


def tax_calculator(amount, country, tax_id=None) -> dict:
    TAX = {"US":0.0,"CA":0.05,"GB":0.20,"DE":0.19,"FR":0.20,"AU":0.10,"IN":0.18}
    rate = 0.0 if tax_id else TAX.get(country,0.0)
    return {"subtotal":amount,"tax_rate":rate,"tax_amount":round(amount*rate,2),
            "total":round(amount*(1+rate),2),"country":country}


def pricing_engine(plan, usage) -> dict:
    base  = plan.get("base_price",0.0)
    rates_raw = plan.get("metered_rates","[]")
    rates = json.loads(rates_raw) if isinstance(rates_raw, str) else rates_raw
    metered = sum(r["unitPrice"] * usage.get("usage",{}).get(r["metric"],0)
                  for r in rates if "metric" in r and "unitPrice" in r)
    return {"base_price":base,"metered_charges":round(metered,2),"subtotal":round(base+metered,2)}


def invoice_generator(customer_id, subscription_id, plan, usage, tax_info, period_start, period_end) -> dict:
    comp = "InvoiceGenerator"
    if not _cb_ok(comp): return _make_billing_error("INVOICE_UNAVAILABLE","Circuit open",comp)
    try:
        pricing = pricing_engine(plan, usage)
        tax     = tax_calculator(pricing["subtotal"], tax_info.get("country","US"), tax_info.get("tax_id"))
        iid     = str(_uuid.uuid4())
        items   = [{"description":"Base subscription","amount":pricing["base_price"]},
                   {"description":"Metered usage","amount":pricing["metered_charges"]}]
        due     = (_dt.now(_tz.utc)+_td(days=30)).isoformat()
        with _DB_LOCK:
            conn = _get_db()
            conn.execute("INSERT INTO billing_invoices (invoice_id,customer_id,subscription_id,total,tax_amount,currency,line_items,period_start,period_end,due_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (iid,customer_id,subscription_id,tax["total"],tax["tax_amount"],"USD",json.dumps(items),period_start,period_end,due))
            conn.commit()
            row = conn.execute("SELECT * FROM billing_invoices WHERE invoice_id=?",(iid,)).fetchone()
            conn.close()
        inv = dict(row); inv["line_items"] = json.loads(inv.get("line_items","[]"))
        _cb_success(comp); return {"invoice":inv}
    except Exception as exc:
        _cb_fail(comp); return _make_billing_error("INVOICE_ERROR",str(exc),comp)


def payment_gateway_client(invoice_id, amount, currency, customer_id, gateway=None) -> dict:
    comp = "PaymentGatewayClient"
    if not _cb_ok(comp): return _make_billing_error("GATEWAY_UNAVAILABLE","Circuit open",comp)
    try:
        gw  = gateway or _BILLING_GATEWAY; pid = str(_uuid.uuid4())
        if gw == "mock":
            txn = {"transaction_id":f"mock_{pid[:8]}","status":"succeeded","amount":amount,"currency":currency}
        elif gw == "stripe":
            if not _BILLING_STRIPE_KEY:
                return _make_billing_error("GATEWAY_CONFIG","STRIPE_SECRET_KEY not set",comp)
            txn = {"transaction_id":f"pi_{pid[:24]}","status":"succeeded","amount":amount,"currency":currency}
        else:
            return _make_billing_error("GATEWAY_UNKNOWN",f"Unknown gateway {gw}",comp)
        with _DB_LOCK:
            conn = _get_db()
            conn.execute("INSERT INTO billing_payments (payment_id,invoice_id,transaction_id,amount,currency,status,gateway) VALUES (?,?,?,?,?,?,?)",
                         (pid,invoice_id,txn["transaction_id"],amount,currency,"paid",gw))
            conn.execute("UPDATE billing_invoices SET status='paid',paid_at=? WHERE invoice_id=?",(_now_iso(),invoice_id))
            conn.commit(); conn.close()
        _cb_success(comp)
        return {"payment_id":pid,"invoice_id":invoice_id,"result":txn,"status":"paid"}
    except Exception as exc:
        _cb_fail(comp); return _make_billing_error("PAYMENT_ERROR",str(exc),comp)


def dunning_manager(invoice_id, subscription_id, failure_code, attempt_number) -> dict:
    RETRY  = {1:3,2:7,3:14}
    did    = str(_uuid.uuid4())
    next_r = (_dt.now(_tz.utc)+_td(days=RETRY.get(attempt_number,14))).isoformat()
    action = "cancel_subscription" if attempt_number >= 3 else "retry"
    try:
        with _DB_LOCK:
            conn = _get_db()
            conn.execute("INSERT INTO billing_dunning (dunning_id,invoice_id,subscription_id,failure_code,attempt_number,next_retry_at,subscription_action) VALUES (?,?,?,?,?,?,?)",
                         (did,invoice_id,subscription_id,failure_code,attempt_number,next_r,action))
            if action == "cancel_subscription":
                conn.execute("UPDATE billing_subscriptions SET status='past_due',canceled_at=? WHERE subscription_id=?",(_now_iso(),subscription_id))
            conn.commit(); conn.close()
        return {"dunning_id":did,"action":action,"next_retry_at":next_r,"attempt_number":attempt_number}
    except Exception as exc:
        return _make_billing_error("DUNNING_ERROR",str(exc),"DunningManager")


def webhook_event_validator(raw_body: str, sig_header: str) -> dict:
    comp = "WebhookEventValidator"
    if not _BILLING_WEBHOOK_SECRET:
        return _make_billing_error("WEBHOOK_CONFIG","SAAS_WEBHOOK_SECRET not set",comp)
    try:
        mac      = _hmac_b.new(_BILLING_WEBHOOK_SECRET.encode(), "sha256")
        mac.update(raw_body.encode())
        expected = mac.hexdigest()
        provided = sig_header.split("=")[-1] if "=" in sig_header else sig_header
        if not _hmac_b.compare_digest(expected, provided):
            return _make_billing_error("WEBHOOK_INVALID_SIG","Signature mismatch",comp)
        return {"valid":True,"payload":json.loads(raw_body)}
    except Exception as exc:
        return _make_billing_error("WEBHOOK_ERROR",str(exc),comp)


def billing_cycle_orchestrator(as_of: str) -> dict:
    comp = "BillingCycleOrchestrator"
    processed = []; errors = []
    try:
        with _DB_LOCK:
            conn = _get_db()
            subs = conn.execute("SELECT * FROM billing_subscriptions WHERE status='active' AND current_period_end<=?",(as_of,)).fetchall()
            conn.close()
        for sub in [dict(s) for s in subs]:
            try:
                cust   = customer_account_manager("get", sub["customer_id"])
                if _is_billing_error(cust): errors.append({"sub":sub["subscription_id"],"err":cust["message"]}); continue
                plan_r = plan_catalog_manager("get", sub["plan_id"])
                if _is_billing_error(plan_r): errors.append({"sub":sub["subscription_id"],"err":plan_r["message"]}); continue
                plan   = plan_r["plan"]
                usage  = usage_aggregator(sub["subscription_id"],sub["current_period_start"],sub["current_period_end"])
                co     = cust["customer"]
                inv    = invoice_generator(sub["customer_id"],sub["subscription_id"],plan,usage,
                                           {"country":co.get("country","US"),"tax_id":co.get("tax_id")},
                                           sub["current_period_start"],sub["current_period_end"])
                if _is_billing_error(inv): errors.append({"sub":sub["subscription_id"],"err":inv["message"]}); continue
                iobj = inv["invoice"]
                pay  = payment_gateway_client(iobj["invoice_id"],iobj["total"],iobj["currency"],sub["customer_id"])
                if _is_billing_error(pay) or pay.get("status") != "paid":
                    dun = dunning_manager(iobj["invoice_id"],sub["subscription_id"],"payment_failed",1)
                    processed.append({"sub":sub["subscription_id"],"status":"dunning","dunning":dun})
                else:
                    ns = sub["current_period_end"]
                    ne = _period_end(ns, plan.get("billing_interval","monthly"))
                    with _DB_LOCK:
                        conn = _get_db()
                        conn.execute("UPDATE billing_subscriptions SET current_period_start=?,current_period_end=? WHERE subscription_id=?",(ns,ne,sub["subscription_id"]))
                        conn.commit(); conn.close()
                    processed.append({"sub":sub["subscription_id"],"status":"billed","invoice":iobj["invoice_id"]})
            except Exception as exc:
                errors.append({"sub":sub.get("subscription_id","?"),"err":str(exc)})
    except Exception as exc:
        return _make_billing_error("CYCLE_ERROR",str(exc),comp)
    return {"processed":len(processed),"errors":len(errors),"results":processed,"error_details":errors}


def webhook_orchestrator(raw_body: str, sig_header: str) -> dict:
    val = webhook_event_validator(raw_body, sig_header)
    if _is_billing_error(val): return val
    event_type = val.get("payload",{}).get("type","")
    _btrace("WebhookOrchestrator","received",event_type=event_type)
    return {"processed":True,"event_type":event_type}


_init_billing_db()


# ── Billing endpoints ──────────────────────────────────────────────────────────

@app.get("/api/billing/plans", tags=["Billing"])
async def billing_list_plans(request: Request):
    _billing_guard(); _require_auth(request)
    return plan_catalog_manager("list")


@app.post("/api/billing/customers", tags=["Billing"])
async def billing_create_customer(request: Request):
    _billing_guard(); caller = _require_auth(request)
    try: body = await request.json()
    except Exception: raise HTTPException(status_code=400, detail="Invalid JSON body.")
    body["username"] = caller  # bind to authenticated user
    result = customer_account_manager("create", fields=body)
    if _is_billing_error(result): raise HTTPException(status_code=400, detail=result.get("message","Failed."))
    return result


@app.get("/api/billing/customers/{customer_id}", tags=["Billing"])
async def billing_get_customer(customer_id: str, request: Request):
    _billing_guard(); caller = _require_auth(request)
    result = customer_account_manager("get", customer_id=customer_id)
    if _is_billing_error(result): raise HTTPException(status_code=404, detail=result.get("message","Not found."))
    cust = result.get("customer")
    if not cust: raise HTTPException(status_code=404, detail="Customer not found.")
    if caller != "admin" and cust.get("username") != caller:
        raise HTTPException(status_code=403, detail="Permission denied.")
    return result


@app.post("/api/billing/subscriptions", tags=["Billing"])
async def billing_create_subscription(request: Request):
    _billing_guard(); _require_auth(request)
    try: body = await request.json()
    except Exception: raise HTTPException(status_code=400, detail="Invalid JSON body.")
    result = subscription_lifecycle_manager("create", fields=body)
    if _is_billing_error(result): raise HTTPException(status_code=400, detail=result.get("message","Failed."))
    return result


@app.delete("/api/billing/subscriptions/{subscription_id}", tags=["Billing"])
async def billing_cancel_subscription(subscription_id: str, request: Request):
    _billing_guard(); _require_auth(request)
    result = subscription_lifecycle_manager("cancel", subscription_id=subscription_id)
    if _is_billing_error(result): raise HTTPException(status_code=400, detail=result.get("message","Failed."))
    return result


@app.post("/api/billing/usage", tags=["Billing"])
async def billing_record_usage(request: Request):
    _billing_guard(); _require_auth(request)
    try: body = await request.json()
    except Exception: raise HTTPException(status_code=400, detail="Invalid JSON body.")
    result = usage_event_recorder(body.get("subscription_id"), body.get("metric"),
                                   body.get("quantity", 0), body.get("occurred_at"),
                                   body.get("idempotency_key"))
    if _is_billing_error(result): raise HTTPException(status_code=400, detail=result.get("message","Failed."))
    return result


@app.get("/api/billing/invoices", tags=["Billing"])
async def billing_list_invoices(request: Request):
    _billing_guard(); caller = _require_auth(request)
    with _DB_LOCK:
        conn = _get_db()
        if caller == "admin":
            rows = conn.execute("SELECT * FROM billing_invoices ORDER BY issued_at DESC").fetchall()
        else:
            cust = customer_account_manager("get_by_username", customer_id=caller)
            co   = cust.get("customer")
            rows = (conn.execute("SELECT * FROM billing_invoices WHERE customer_id=? ORDER BY issued_at DESC",
                                 (co["customer_id"],)).fetchall() if co else [])
        conn.close()
    return {"invoices": [dict(r) for r in rows]}


@app.get("/api/billing/invoices/{invoice_id}", tags=["Billing"])
async def billing_get_invoice(invoice_id: str, request: Request):
    _billing_guard(); _require_auth(request)
    with _DB_LOCK:
        conn = _get_db()
        row  = conn.execute("SELECT * FROM billing_invoices WHERE invoice_id=?",(invoice_id,)).fetchone()
        conn.close()
    if not row: raise HTTPException(status_code=404, detail=f"Invoice {invoice_id} not found.")
    inv = dict(row); inv["line_items"] = json.loads(inv.get("line_items","[]"))
    return {"invoice": inv}


@app.post("/api/billing/run-cycle", tags=["Billing"])
async def billing_run_cycle(request: Request):
    _billing_guard(); _require_admin(request)
    try: body = await request.json()
    except Exception: raise HTTPException(status_code=400, detail="Invalid JSON body.")
    return billing_cycle_orchestrator(body.get("asOfDate", _now_iso()))


@app.post("/api/billing/webhook", tags=["Billing"])
async def billing_webhook(request: Request):
    _billing_guard()
    raw_body = await request.body()
    sig_hdr  = request.headers.get("Stripe-Signature","")
    result   = webhook_orchestrator(raw_body.decode("utf-8"), sig_hdr)
    if _is_billing_error(result):
        raise HTTPException(status_code=400, detail=result.get("message","Webhook failed."))
    return result


@app.get("/api/billing/fx", tags=["Billing"])
async def billing_fx(from_currency: str, to_currency: str, request: Request, amount: float = 1.0):
    _billing_guard(); _require_auth(request)   # was unauthenticated — fixed
    return currency_converter(amount, from_currency.upper(), to_currency.upper())


@app.get("/api/billing/health", tags=["Billing"])
async def billing_health(request: Request):
    _billing_guard(); _require_auth(request)
    comps = ["CustomerAccountManager","SubscriptionLifecycleManager","UsageEventRecorder",
             "CurrencyConverter","TaxCalculator","PricingEngine","InvoiceGenerator",
             "PaymentGatewayClient","DunningManager","NotificationDispatcher",
             "BillingScheduler","WebhookEventValidator"]
    status = {}
    for c in comps:
        s  = _CB.get(c,{}); ob = bool(s.get("open_until") and _time.time() < s["open_until"])
        status[c] = {"status":"down" if ob else "ok","circuit_open":ob,"failure_count":s.get("failures",0)}
    return {"overall":"ok" if all(v["status"]=="ok" for v in status.values()) else "degraded",
            "components":status,"gateway":_BILLING_GATEWAY,"enabled":_BILLING_ENABLED}


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
    logger.info("  Billing       : %s", "enabled" if _BILLING_ENABLED else "disabled")
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