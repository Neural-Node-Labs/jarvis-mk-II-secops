"""
core/security.py — Production Security Layer
=============================================
Centralises every security concern that was scattered across main.py:

  - Rate limiting          (login brute-force, API flooding)
  - Request size guard     (upload bomb prevention)
  - Auth helpers           (_require_auth, _require_admin, _require_ws_auth)
  - Input sanitisation     (filenames, paths, shell command guard)
  - CORS enforcement       (disallow wildcard + credentials)
  - Session eviction       (LRU cap on _sessions dict)
  - Output scrubbing       (strip internal paths from responses)
  - Security headers       (CSP, HSTS, X-Frame-Options, etc.)

All functions are imported by main.py. Nothing here starts threads or
opens files at import time — safe to import in tests.

version: 1.0.0
CBD Contract:
  Name:             SecurityLayer
  Logical Function: Defence-in-depth for every inbound request
  IN:  FastAPI Request / raw values
  OUT: validated values OR HTTPException(401/403/413/429)
  Errors: all raised as HTTPException with safe (non-leaking) messages
"""

import hashlib
import hmac
import logging
import os
import re
import secrets
import time
import threading
from collections import defaultdict, deque
from typing import Optional

from fastapi import HTTPException, Request, WebSocket

logger = logging.getLogger("jarvis.security")

# ── Constants (all overridable via env) ────────────────────────────────────────

# Rate limiting
LOGIN_RATE_WINDOW_S   = int(os.getenv("SEC_LOGIN_WINDOW_S",   "60"))
LOGIN_RATE_MAX        = int(os.getenv("SEC_LOGIN_MAX",         "10"))   # attempts per window
API_RATE_WINDOW_S     = int(os.getenv("SEC_API_WINDOW_S",     "10"))
API_RATE_MAX          = int(os.getenv("SEC_API_MAX",           "60"))   # requests per window
WS_RATE_MAX           = int(os.getenv("SEC_WS_MAX",            "30"))   # messages per window

# WebSocket
WS_AUTH_TIMEOUT_S     = int(os.getenv("SEC_WS_AUTH_TIMEOUT",  "10"))   # seconds to send auth frame
WS_MAX_MSG_BYTES      = int(os.getenv("SEC_WS_MAX_MSG_BYTES",  str(1 * 1024 * 1024)))  # 1 MB

# Upload
UPLOAD_MAX_BYTES      = int(os.getenv("SEC_UPLOAD_MAX_BYTES",  str(10 * 1024 * 1024))) # 10 MB
UPLOAD_MAX_FILES      = int(os.getenv("SEC_UPLOAD_MAX_FILES",  "10"))
UPLOAD_ALLOWED_MIMES  = set(os.getenv("SEC_UPLOAD_MIMES", "").split(",")) or None  # None = allow all

# Session
SESSION_MAX           = int(os.getenv("SEC_SESSION_MAX",       "500"))  # LRU cap

# Input
MAX_MESSAGE_LEN       = int(os.getenv("SEC_MAX_MESSAGE_LEN",   "32000"))
MAX_FILENAME_LEN      = int(os.getenv("SEC_MAX_FILENAME_LEN",  "255"))

# Kali exec output cap (bytes)
KALI_OUTPUT_CAP       = int(os.getenv("SEC_KALI_OUTPUT_CAP",   str(512 * 1024)))  # 512 KB


# ── Rate limiter ───────────────────────────────────────────────────────────────

class _RateBucket:
    """Thread-safe sliding-window rate limiter per key."""

    def __init__(self, window_s: int, max_hits: int):
        self._window  = window_s
        self._max     = max_hits
        self._buckets: dict[str, deque] = defaultdict(deque)
        self._lock    = threading.Lock()

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            dq = self._buckets[key]
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= self._max:
                return False
            dq.append(now)
            return True

    def clear(self, key: str) -> None:
        with self._lock:
            self._buckets.pop(key, None)


_login_limiter = _RateBucket(LOGIN_RATE_WINDOW_S, LOGIN_RATE_MAX)
_api_limiter   = _RateBucket(API_RATE_WINDOW_S,   API_RATE_MAX)
_ws_limiter    = _RateBucket(API_RATE_WINDOW_S,   WS_RATE_MAX)


def check_login_rate(ip: str) -> None:
    """Raise 429 if this IP has exceeded the login rate limit."""
    if not _login_limiter.is_allowed(ip):
        logger.warning("[rate_limit_login] ip=%s", ip)
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Please wait before trying again.",
            headers={"Retry-After": str(LOGIN_RATE_WINDOW_S)},
        )


def check_api_rate(ip: str) -> None:
    """Raise 429 if this IP has exceeded the general API rate limit."""
    if not _api_limiter.is_allowed(ip):
        logger.warning("[rate_limit_api] ip=%s", ip)
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded.",
            headers={"Retry-After": str(API_RATE_WINDOW_S)},
        )


def check_ws_rate(user_id: str) -> None:
    """Raise 429 if this WebSocket user has sent too many messages."""
    if not _ws_limiter.is_allowed(user_id):
        logger.warning("[rate_limit_ws] user=%s", user_id)
        raise HTTPException(status_code=429, detail="WebSocket message rate limit exceeded.")


def clear_login_rate(ip: str) -> None:
    """Reset login counter after a successful login (avoid penalising legit users)."""
    _login_limiter.clear(ip)


# ── Auth helpers ───────────────────────────────────────────────────────────────

def get_client_ip(request: Request) -> str:
    """
    Extract the real client IP, respecting X-Forwarded-For from trusted proxies.
    Falls back to direct connection IP.
    """
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        # Rightmost IP added by our own proxy is trustworthy
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def require_auth(request: Request, validate_token_fn) -> str:
    """
    Extract and validate Bearer token from Authorization header.
    Returns username or raises 401.

    Parameters
    ----------
    validate_token_fn : callable(token: str) -> str | None
        The DB lookup function from main.py (_validate_token).
    """
    hdr = request.headers.get("Authorization", "")
    if not hdr.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or malformed Authorization header.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = hdr[7:].strip()
    if not token or len(token) > 128:
        raise HTTPException(status_code=401, detail="Invalid token format.")
    username = validate_token_fn(token)
    if not username:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return username


def require_admin(username: str) -> None:
    """Raise 403 if username is not 'admin'."""
    if username != "admin":
        raise HTTPException(status_code=403, detail="Administrator access required.")


def require_ws_auth(token: str, validate_token_fn) -> str:
    """
    Validate a WebSocket auth token.
    Returns username or raises 401 (caller should close the socket).
    """
    if not token or len(token) > 128:
        raise HTTPException(status_code=401, detail="Invalid WS token.")
    username = validate_token_fn(token)
    if not username:
        raise HTTPException(status_code=401, detail="Invalid or expired WS token.")
    return username


def require_kali_key(request: Request, expected_key: str) -> None:
    """
    Validate the X-Kali-Key header against the configured key.
    ALWAYS raises 403 when expected_key is empty (forces explicit configuration).
    """
    if not expected_key:
        logger.error("[kali_auth] KALI_EXEC_API_KEY is not set — endpoint is disabled")
        raise HTTPException(
            status_code=503,
            detail="Kali execution is disabled. Set KALI_EXEC_API_KEY in environment.",
        )
    provided = request.headers.get("X-Kali-Key", "")
    if not hmac.compare_digest(provided, expected_key):
        logger.warning("[kali_auth_fail] ip=%s", get_client_ip(request))
        raise HTTPException(status_code=403, detail="Invalid Kali execution key.")


def require_debug_disabled() -> None:
    """Raise 404 if DEBUG_ENDPOINTS env is not explicitly 'true'."""
    if os.getenv("JARVIS_DEBUG_ENDPOINTS", "false").lower() != "true":
        raise HTTPException(status_code=404, detail="Not found.")


# ── Input sanitisation ─────────────────────────────────────────────────────────

_SAFE_FILENAME_RE  = re.compile(r"[^\w\-. ]")
_PATH_TRAVERSAL_RE = re.compile(r"\.\./|\.\.\\")


def sanitise_filename(name: str) -> str:
    """
    Strip path components and dangerous characters from an uploaded filename.
    Returns a safe basename.
    """
    if not name:
        return "upload"
    # Strip any directory component
    base = os.path.basename(name.replace("\\", "/"))
    # Remove null bytes and path separators
    base = base.replace("\x00", "").replace("/", "").replace("\\", "")
    # Replace remaining unsafe chars with underscore
    base = _SAFE_FILENAME_RE.sub("_", base)
    return base[:MAX_FILENAME_LEN] or "upload"


def sanitise_message(message: str) -> str:
    """Truncate oversized messages. Does not modify content (LLM handles interpretation)."""
    if len(message) > MAX_MESSAGE_LEN:
        logger.warning("[msg_truncated] original_len=%d", len(message))
        return message[:MAX_MESSAGE_LEN]
    return message


def validate_workspace_path(abs_path: str, ws_root: str) -> str:
    """
    Ensure abs_path is inside ws_root after symlink resolution.
    Raises 403 if path escapes the workspace.
    Returns the resolved absolute path.
    """
    # Resolve symlinks to prevent symlink traversal
    try:
        resolved = os.path.realpath(abs_path)
        root_resolved = os.path.realpath(ws_root)
    except Exception:
        raise HTTPException(status_code=403, detail="Invalid path.")

    if not resolved.startswith(root_resolved + os.sep) and resolved != root_resolved:
        logger.warning("[path_traversal] attempted=%s root=%s", abs_path, ws_root)
        raise HTTPException(status_code=403, detail="Path outside workspace.")
    return resolved


def validate_upload_size(content_length: Optional[int], content: bytes) -> None:
    """Raise 413 if the upload exceeds the configured limit."""
    size = len(content) if content else (content_length or 0)
    if size > UPLOAD_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Upload too large. Maximum {UPLOAD_MAX_BYTES // (1024*1024)} MB.",
        )


def validate_upload_mime(mime: str, filename: str) -> None:
    """
    Validate MIME type against allowlist (if configured).
    Also blocks dangerous extension/MIME combinations.
    """
    dangerous_exts = {".exe", ".dll", ".so", ".sh", ".bat", ".ps1", ".vbs",
                      ".msi", ".dmg", ".pkg", ".deb", ".rpm", ".elf"}
    ext = os.path.splitext(filename)[1].lower() if filename else ""

    if ext in dangerous_exts:
        raise HTTPException(
            status_code=422,
            detail=f"File type '{ext}' is not permitted.",
        )

    if UPLOAD_ALLOWED_MIMES and mime not in UPLOAD_ALLOWED_MIMES:
        raise HTTPException(
            status_code=422,
            detail=f"MIME type '{mime}' is not permitted.",
        )


def scrub_abs_paths(data: dict) -> dict:
    """
    Remove or redact absolute filesystem paths from response dicts.
    Replaces /app/... paths with relative equivalents to avoid leaking
    container internals to clients.
    """
    workspace_root = os.getenv("JARVIS_WORKSPACE_ROOT", "/app/workspace")

    def _scrub(value):
        if isinstance(value, str):
            if value.startswith(workspace_root):
                return value[len(workspace_root):].lstrip("/") or "/"
            # Redact any other /app/ paths
            if value.startswith("/app/"):
                return "<internal>"
            return value
        if isinstance(value, dict):
            return {k: _scrub(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_scrub(i) for i in value]
        return value

    return _scrub(data)


# ── Session eviction ───────────────────────────────────────────────────────────

class BoundedSessionStore:
    """
    Thread-safe LRU session store with a hard cap.
    When the cap is hit, the least-recently-used session is evicted.

    Parameters
    ----------
    max_sessions : int
        Maximum number of concurrent sessions. Oldest LRU evicted beyond this.
    """

    def __init__(self, max_sessions: int = SESSION_MAX):
        self._max    = max_sessions
        self._store: dict[str, object] = {}
        self._order: deque = deque()      # LRU order (oldest at left)
        self._lock   = threading.Lock()

    def get(self, user_id: str) -> Optional[object]:
        with self._lock:
            agent = self._store.get(user_id)
            if agent is not None:
                # Move to MRU position
                try:
                    self._order.remove(user_id)
                except ValueError:
                    pass
                self._order.append(user_id)
            return agent

    def set(self, user_id: str, agent: object) -> None:
        with self._lock:
            if user_id in self._store:
                try:
                    self._order.remove(user_id)
                except ValueError:
                    pass
            elif len(self._store) >= self._max:
                # Evict LRU
                evict = self._order.popleft()
                self._store.pop(evict, None)
                logger.info("[session_evicted] user=%s", evict)
            self._store[user_id] = agent
            self._order.append(user_id)

    def pop(self, user_id: str) -> bool:
        with self._lock:
            if user_id in self._store:
                del self._store[user_id]
                try:
                    self._order.remove(user_id)
                except ValueError:
                    pass
                return True
            return False

    def __contains__(self, user_id: str) -> bool:
        with self._lock:
            return user_id in self._store

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


# ── Security headers middleware ────────────────────────────────────────────────

async def security_headers_middleware(request: Request, call_next):
    """
    ASGI middleware: add security headers to every response.
    Register with: app.middleware("http")(security_headers_middleware)
    """
    response = await call_next(request)
    response.headers["X-Content-Type-Options"]    = "nosniff"
    response.headers["X-Frame-Options"]           = "DENY"
    response.headers["X-XSS-Protection"]          = "1; mode=block"
    response.headers["Referrer-Policy"]           = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"]        = "geolocation=(), microphone=(self), camera=()"

    # HSTS — only set if TLS is expected (controlled by env)
    if os.getenv("JARVIS_HTTPS", "false").lower() == "true":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    # Content-Security-Policy — tighten in production
    csp_mode = os.getenv("JARVIS_CSP_MODE", "report")  # "enforce" or "report"
    csp = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "    # inline needed for Vite dev
        "style-src 'self' 'unsafe-inline'; "
        "connect-src 'self' ws: wss:; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self';"
    )
    header_name = (
        "Content-Security-Policy"
        if csp_mode == "enforce"
        else "Content-Security-Policy-Report-Only"
    )
    response.headers[header_name] = csp

    # Remove headers that leak server details
    # MutableHeaders does not support .pop() — use del with existence check
    for _hdr in ("Server", "X-Powered-By"):
        if _hdr in response.headers:
            del response.headers[_hdr]

    return response


# ── CORS enforcement ───────────────────────────────────────────────────────────

def get_cors_origins() -> list[str]:
    """
    Return the list of allowed CORS origins.
    Raises a startup warning if wildcard + credentials is configured
    (browsers reject this combination per the CORS spec).
    """
    raw = os.getenv("CORS_ORIGINS", "").strip()
    if not raw or raw == "*":
        if os.getenv("JARVIS_ENV", "production") != "development":
            logger.warning(
                "[cors_warning] CORS_ORIGINS is '*' — this is insecure. "
                "Set CORS_ORIGINS to your frontend origin (e.g. https://app.example.com)."
            )
        # Return empty list → FastAPI CORSMiddleware will not add any ACAO header
        # which is correct for a same-origin deployment behind nginx.
        return []
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    return origins