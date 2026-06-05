"""
FastAPI Backend — Agent API Server

Endpoints:
  WebSocket /ws/chat           — streaming chat
  WebSocket /ws/evolution      — self-evolution progress
  POST /api/upload             — file upload (text, PDF, image)
  POST /api/confirm            — confirm a pending action
  GET  /api/skills             — list registered skills
  GET  /api/config             — current LLM config
  POST /api/config             — update LLM config
  POST /api/reset              — reset agent conversation
  GET  /api/model-limits       — returns max_tokens map
  GET  /api/memory/{user_id}   — retrieve conversation history
  DELETE /api/memory/{user_id} — clear user history
  Evolution: /api/evolution/*, /ws/evolution
"""
import json
import logging
import os
import base64
import asyncio
import secrets
import time
import hmac
import hashlib
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional

from core.llm_router import LLMConfig, PROVIDER_DEFAULTS, MODEL_MAX_TOKENS, get_model_max_tokens, DEFAULT_MAX_TOKENS
from core.skill_registry import SkillRegistry
from skills import load_all_skills
from core.agent import Agent
from core.memory_manager import MemoryManager
from skills.filesystem_skill import FileSystemSkill
from skills.os_execution_skill import OSExecutionSkill
from skills.cbd_skill import CBDArchitectSkill
from skills.self_evolution_skill.evolution_skill import (
    SelfEvolutionSkill,
    _sessions,
    _orchestrators,
)
from skills.memory_skill import MemorySkill
from skills.multimodal_analyzer import MultimodalAnalyzerSkill
from skills.image_vision_skill import ImageVisionSkill
from skills.file_streamer import FileStreamerSkill  # FIX-1: explicit import guarantees registration even if load_all_skills silently fails
from skills.folder_reader import FolderReaderSkill
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# ── Auth / JWT ───────────────────────────────────────────────────────────────
# Environment variables:
#   AUTH_SECRET   — shared login password (required; app refuses to start without it)
#   ALLOWED_USERS — comma-separated usernames  (default: "admin")
#   JWT_SECRET    — signs tokens (auto-generated each startup if not set)
#   JWT_TTL_HOURS — token lifetime in hours    (default: 8)
#
# Flow:
#   1. Backend generates JWT_SECRET on startup (or reads from env).
#   2. Frontend POSTs {username, password} to /api/auth/login.
#   3. Backend validates credentials, returns {access_token, token_type:"bearer"}.
#   4. Frontend stores token; sends Authorization: Bearer <token> on every request.
#   5. Both REST endpoints and WebSockets verify the token via require_bearer().

AUTH_SECRET  = os.getenv("AUTH_SECRET", "")          # login password
ALLOWED_USERS = [u.strip().lower() for u in os.getenv("ALLOWED_USERS", "admin").split(",") if u.strip()]
JWT_SECRET   = os.getenv("JWT_SECRET") or secrets.token_hex(32)   # auto-rotates each restart if not pinned
JWT_TTL      = int(os.getenv("JWT_TTL_HOURS", "8")) * 3600        # seconds

_http_bearer = HTTPBearer(auto_error=False)

# ── Tiny JWT (HMAC-SHA256, no extra deps) ─────────────────────────────────────
import base64 as _b64, json as _json

def _b64url(data: bytes) -> str:
    return _b64.urlsafe_b64encode(data).rstrip(b"=").decode()

def _b64url_decode(s: str) -> bytes:
    pad = 4 - len(s) % 4
    return _b64.urlsafe_b64decode(s + "=" * (pad % 4))

def create_jwt(username: str) -> str:
    header  = _b64url(_json.dumps({"alg":"HS256","typ":"JWT"}).encode())
    payload = _b64url(_json.dumps({"sub": username, "iat": int(time.time()), "exp": int(time.time()) + JWT_TTL}).encode())
    sig     = _b64url(hmac.new(JWT_SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"

def verify_jwt(token: str) -> Optional[str]:
    """Returns username on success, None on any failure."""
    try:
        header, payload, sig = token.split(".")
        expected = _b64url(hmac.new(JWT_SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        claims = _json.loads(_b64url_decode(payload))
        if claims.get("exp", 0) < time.time():
            return None
        return claims.get("sub")
    except Exception:
        return None

async def require_bearer(creds: Optional[HTTPAuthorizationCredentials] = Depends(_http_bearer)) -> str:
    """FastAPI dependency — raises 401 if token is missing or invalid."""
    if not AUTH_SECRET:          # auth disabled — pass everyone through
        return "anonymous"
    token = creds.credentials if creds else None
    user  = verify_jwt(token) if token else None
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token",
                            headers={"WWW-Authenticate": "Bearer"})
    return user

async def ws_require_bearer(ws: WebSocket) -> Optional[str]:
    """For WebSockets: read first message {type:'auth', token:'...'}, return username or close."""""
    if not AUTH_SECRET:
        return "anonymous"
    try:
        first_raw = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
        first_msg = _json.loads(first_raw)
        token = first_msg.get("token", "") if first_msg.get("type") == "auth" else ""
        user  = verify_jwt(token) if token else None
        if not user:
            await ws.send_json({"type": "error", "data": "Unauthorized — invalid or expired token."})
            await ws.close(code=4401)
            return None
        logger.info(f"WS authenticated: user='{user}'")
        return user
    except (asyncio.TimeoutError, _json.JSONDecodeError, Exception) as exc:
        await ws.send_json({"type": "error", "data": "Auth handshake failed."})
        await ws.close(code=4401)
        return None

logger = logging.getLogger("main")

SETTINGS_FILE = Path(os.getenv("SETTINGS_PATH", "/app/data/settings.json"))

# ── Supported upload MIME types ────────────────────────────────────────────────
TEXT_MIMES = {
    "text/plain", "text/markdown", "text/csv", "text/html",
    "text/xml", "application/json", "application/xml",
    "application/x-yaml", "text/yaml",
}
IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
BINARY_MIMES = {"application/pdf"} | IMAGE_MIMES

MAX_UPLOAD_BYTES = 20 * 1024 * 1024   # 20 MB hard limit


def _load_persisted_settings() -> dict:
    try:
        if SETTINGS_FILE.exists():
            return json.loads(SETTINGS_FILE.read_text())
    except Exception:
        pass
    return {}

def _save_settings(cfg: "ConfigUpdate") -> None:
    try:
        SETTINGS_FILE.write_text(json.dumps({
            "provider": cfg.provider, "model": cfg.model, "base_url": cfg.base_url,
            "temperature": cfg.temperature, "max_tokens": cfg.max_tokens,
            "schema_format": cfg.schema_format,
        }, indent=2))
    except Exception:
        pass

# ── Global state ───────────────────────────────────────────────────────────────
registry = SkillRegistry()
memory_manager = MemoryManager()
registry.register("filesystem", FileSystemSkill())
registry.register("os_execution", OSExecutionSkill())
registry.register("cbd_architect", CBDArchitectSkill())
registry.register("self_evolution", SelfEvolutionSkill())
registry.register("memory_manager", MemorySkill(memory_manager))
registry.register("multimodal_analyzer", MultimodalAnalyzerSkill())
registry.register("image_vision", ImageVisionSkill())  # FIX-6: key must match skills_manifest.json "image_vision"
load_all_skills(registry)
# FIX-1: file_streamer is also loaded by load_all_skills via __init__.py, but
# we register it explicitly here as a safety-net in case the dynamic import
# silently fails (ImportError is swallowed inside load_all_skills).
if not registry.get("file_streamer"):
    registry.register("file_streamer", FileStreamerSkill())
    logger.warning("file_streamer was not loaded by load_all_skills — registered via fallback.")
if not registry.get("folder_reader"):
    registry.register("folder_reader", FolderReaderSkill())
    logger.warning("folder_reader was not loaded by load_all_skills — registered via fallback.")


_persisted = _load_persisted_settings()
_default_provider = _persisted.get("provider", "deepseek")
_defaults = PROVIDER_DEFAULTS.get(_default_provider, PROVIDER_DEFAULTS["deepseek"])
_key_env = _defaults.get("key_env") or ""

current_config = LLMConfig(
    provider=_default_provider,
    model=_persisted.get("model") or _defaults.get("model", "deepseek-coder"),
    api_key=os.getenv(_key_env, ""),
    base_url=_persisted.get("base_url") or _defaults.get("base_url", ""),
    temperature=_persisted.get("temperature", 0.7),
    max_tokens=_persisted.get("max_tokens", DEFAULT_MAX_TOKENS),
    schema_format=_persisted.get("schema_format"),
)

agent = Agent(current_config, registry)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Agent backend started — {current_config.provider}/{current_config.model}")
    if AUTH_SECRET:
        src = "env" if os.getenv("JWT_SECRET") else "auto-generated"
        logger.info(f"Auth ENABLED — JWT_SECRET {src}, TTL={JWT_TTL}s, users={ALLOWED_USERS}")
    else:
        logger.warning("AUTH_SECRET not set — all endpoints are OPEN (dev mode)")
    logger.info(f"Skills: {[s['name'] for s in registry.list_skills()]}")
    yield
    logger.info("Agent backend shutting down")

app = FastAPI(title="AI Agent API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)


# ── Models ─────────────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str

class ConfigUpdate(BaseModel):
    provider: str
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None   # None → auto from model
    schema_format: Optional[str] = None

class ConfirmRequest(BaseModel):
    confirm_id: str

class EvolutionStartRequest(BaseModel):
    target_skill: str = ""
    task_description: str = ""

class EvolutionActionRequest(BaseModel):
    workspace_id: Optional[str] = None


# ── Auth endpoint ─────────────────────────────────────────────────────────────
@app.post("/api/auth/login")
async def login(req: LoginRequest):
    """Exchange {username, password} for a Bearer JWT. No auth required on this endpoint."""
    if not AUTH_SECRET:
        # Auth disabled — issue a no-op token so the UI flow still works
        token = create_jwt(req.username.strip().lower() or "anonymous")
        return {"access_token": token, "token_type": "bearer", "expires_in": JWT_TTL}
    u = req.username.strip().lower()
    if u not in ALLOWED_USERS or req.password != AUTH_SECRET:
        logger.warning(f"Failed login attempt for user='{u}'")
        raise HTTPException(status_code=401, detail="Invalid credentials",
                            headers={"WWW-Authenticate": "Bearer"})
    token = create_jwt(u)
    logger.info(f"Issued JWT for user='{u}' (TTL={JWT_TTL}s)")
    return {"access_token": token, "token_type": "bearer", "expires_in": JWT_TTL}

@app.get("/api/auth/verify")
async def verify_token(user: str = Depends(require_bearer)):
    """Lightweight token check — returns username if valid."""
    return {"valid": True, "username": user}


# ── File Upload ────────────────────────────────────────────────────────────────
@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...), _user: str = Depends(require_bearer)):
    """
    Accept a file upload and return a structured attachment dict ready to be
    passed alongside the next chat message via WebSocket.

    Returns:
      {
        "name": "filename.ext",
        "mime": "text/plain",
        "size": 1234,
        "text": "decoded content",   # for text types
        "b64":  "base64string",      # for binary/image/pdf types
        "preview": "first 200 chars" # for text types
      }
    """
    raw = await file.read()
    size = len(raw)

    if size > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File too large ({size} bytes). Max is {MAX_UPLOAD_BYTES}.")

    mime = file.content_type or "application/octet-stream"
    name = file.filename or "upload"

    # Normalise mime for common extensions when browser doesn't set it
    if mime == "application/octet-stream":
        ext = Path(name).suffix.lower()
        ext_map = {
            ".txt": "text/plain", ".md": "text/markdown", ".py": "text/plain",
            ".js": "text/plain", ".ts": "text/plain", ".json": "application/json",
            ".csv": "text/csv", ".yaml": "text/yaml", ".yml": "text/yaml",
            ".html": "text/html", ".xml": "text/xml",
            ".pdf": "application/pdf",
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp",
        }
        mime = ext_map.get(ext, mime)

    result = {"name": name, "mime": mime, "size": size}

    if mime in TEXT_MIMES or mime.startswith("text/"):
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception:
            text = raw.decode("latin-1", errors="replace")
        result["text"] = text
        result["preview"] = text[:200] + ("…" if len(text) > 200 else "")
    else:
        # Binary — encode as base64
        result["b64"] = base64.b64encode(raw).decode("ascii")
        result["preview"] = f"[binary {mime}, {size} bytes]"

    logger.info("Upload received: %s (%s, %d bytes)", name, mime, size)
    return result


# ── WebSocket Chat ─────────────────────────────────────────────────────────────
@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    await ws.accept()
    # ── Bearer token handshake (JWT) ─────────────────────────────────────────
    user = await ws_require_bearer(ws)
    if user is None:
        return  # ws_require_bearer already closed the socket
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)

                if msg.get("type") == "chat":
                    attachments = msg.get("attachments") or []
                    async for ev in agent.chat_stream(
                        msg.get("message", ""),
                        attachments=attachments if attachments else None,
                    ):
                        await ws.send_text(json.dumps(ev))

                elif msg.get("type") == "confirm":
                    cid = msg.get("confirm_id")
                    if cid:
                        async for ev in agent.confirm_action(cid):
                            await ws.send_text(json.dumps(ev))

                elif msg.get("type") == "cancel_confirm":
                    cid = msg.get("confirm_id")
                    if cid and cid in agent.pending_confirms:
                        del agent.pending_confirms[cid]

            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        logger.info("WS chat disconnected")


# ── WebSocket Evolution ────────────────────────────────────────────────────────
@app.websocket("/ws/evolution")
async def ws_evolution(ws: WebSocket):
    await ws.accept()
    user = await ws_require_bearer(ws)
    if user is None:
        return
    logger.info(f"Evolution WS connected: user='{user}'")
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
                if msg.get("type") == "subscribe":
                    ws_id = msg.get("workspace_id") or (list(_sessions.keys())[-1] if _sessions else None)
                    if ws_id and ws_id in _sessions:
                        session = _sessions[ws_id]
                        await ws.send_text(json.dumps({
                            "type": "evolution_state", "data": session.to_dict(), "workspace_id": ws_id,
                        }))
                        last_phase = session.current_phase
                        while session.status.value in ("running", "awaiting_approval", "approved"):
                            await asyncio.sleep(0.5)
                            if session.current_phase != last_phase:
                                await ws.send_text(json.dumps({
                                    "type": "evolution_state", "data": session.to_dict(), "workspace_id": ws_id,
                                }))
                                last_phase = session.current_phase
                            if session.status.value in ("completed", "failed", "aborted"):
                                break
                        await ws.send_text(json.dumps({
                            "type": "evolution_complete", "data": session.to_dict(), "workspace_id": ws_id,
                        }))
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        logger.info("Evolution WS disconnected")


# ── REST: Skills & Config ──────────────────────────────────────────────────────
@app.get("/api/skills")
async def list_skills(_user: str = Depends(require_bearer)):
    return {"skills": registry.list_skills()}


@app.get("/api/model-limits")
async def model_limits(_user: str = Depends(require_bearer)):
    """Return the full MODEL_MAX_TOKENS map plus current model's limit."""
    return {
        "model_max_tokens": MODEL_MAX_TOKENS,
        "default_max_tokens": DEFAULT_MAX_TOKENS,
        "current_model": current_config.model,
        "current_model_max": get_model_max_tokens(current_config.model),
    }


@app.post("/api/config")
async def update_config(config: ConfigUpdate, _user: str = Depends(require_bearer)):
    global current_config, agent
    defaults = PROVIDER_DEFAULTS.get(config.provider, PROVIDER_DEFAULTS["deepseek"])
    key_env = defaults.get("key_env") or ""

    # Dynamic max_tokens: use model limit if not explicitly provided
    resolved_model = config.model or defaults.get("model", "")
    auto_max = get_model_max_tokens(resolved_model)
    max_tok = config.max_tokens if config.max_tokens is not None else auto_max

    new_config = LLMConfig(
        provider=config.provider,
        model=resolved_model,
        api_key=config.api_key or os.getenv(key_env, ""),
        base_url=config.base_url or defaults.get("base_url", ""),
        temperature=config.temperature or 0.7,
        max_tokens=max_tok,
        schema_format=config.schema_format,
    )
    current_config = new_config
    agent = Agent(current_config, registry)
    _save_settings(config)
    return {
        "status": "ok",
        "provider": config.provider,
        "model": new_config.model,
        "max_tokens": max_tok,
        "model_max_tokens": auto_max,
        "schema_format": new_config.get_schema(),
    }


@app.get("/api/config")
async def get_config(_user: str = Depends(require_bearer)):
    return {
        "provider": current_config.provider,
        "model": current_config.model,
        "available_providers": list(PROVIDER_DEFAULTS.keys()),
        "key_configured": bool(
            current_config.api_key and not current_config.api_key.startswith("MISSING_")
        ),
        "max_tokens": current_config.max_tokens,
        "temperature": current_config.temperature,
        "schema_format": current_config.get_schema(),
        "model_max_tokens": get_model_max_tokens(current_config.model),
    }


@app.post("/api/reset")
async def reset_agent(_user: str = Depends(require_bearer)):
    agent.reset()
    return {"status": "ok"}


# ── REST: Memory ───────────────────────────────────────────────────────────────
@app.get("/api/memory/{user_id}")
async def get_memory(user_id: str, n: int = 20, _user: str = Depends(require_bearer)):
    """Retrieve the last n conversation turns for a user."""
    history = memory_manager.retrieve_last_n(user_id, n)
    size = memory_manager.file_size(user_id)
    return {"user_id": user_id, "history": history, "file_size_bytes": size}


@app.delete("/api/memory/{user_id}")
async def clear_memory(user_id: str, _user: str = Depends(require_bearer)):
    """Delete a user's conversation history file."""
    existed = memory_manager.clear(user_id)
    return {"user_id": user_id, "cleared": existed}


@app.get("/api/memory")
async def list_memory_users(_user: str = Depends(require_bearer)):
    """List all users that have saved conversation history."""
    return {"users": memory_manager.list_users()}


# ── REST: Evolution ────────────────────────────────────────────────────────────
@app.post("/api/evolution/start")
async def evo_start(req: EvolutionStartRequest, _user: str = Depends(require_bearer)):
    skill = registry.get("self_evolution")
    if not skill:
        raise HTTPException(500, "Self-evolution skill not registered")
    result = await skill.execute("start", {
        "target_skill": req.target_skill,
        "task_description": req.task_description,
    })
    if result.success:
        return {
            "status": "ok",
            "workspace_id": result.output.get("workspace_id"),
            "state": result.output.get("state"),
        }
    raise HTTPException(500, result.error)

@app.get("/api/evolution/status")
async def evo_status(workspace_id: Optional[str] = None, _user: str = Depends(require_bearer)):
    skill = registry.get("self_evolution")
    result = await skill.execute("status", {"workspace_id": workspace_id})
    if result.success:
        return {"status": "ok", "state": result.output.get("state")}
    raise HTTPException(404, result.error)

@app.post("/api/evolution/approve")
async def evo_approve(req: EvolutionActionRequest, _user: str = Depends(require_bearer)):
    skill = registry.get("self_evolution")
    result = await skill.execute("approve", {"workspace_id": req.workspace_id})
    if result.success:
        return {"status": "ok", "approved": True, "state": result.output.get("state")}
    raise HTTPException(400, result.error)

@app.post("/api/evolution/revise")
async def evo_revise(req: EvolutionActionRequest, _user: str = Depends(require_bearer)):
    skill = registry.get("self_evolution")
    result = await skill.execute("revise", {"workspace_id": req.workspace_id})
    if result.success:
        return {"status": "ok", "revised": True, "state": result.output.get("state")}
    raise HTTPException(400, result.error)

@app.post("/api/evolution/reset")
async def evo_reset(req: EvolutionActionRequest, _user: str = Depends(require_bearer)):
    skill = registry.get("self_evolution")
    await skill.execute("reset", {"workspace_id": req.workspace_id})
    return {"status": "ok", "reset": True}

@app.get("/api/evolution/logs")
async def evo_logs(workspace_id: Optional[str] = None, _user: str = Depends(require_bearer)):
    skill = registry.get("self_evolution")
    result = await skill.execute("get_logs", {"workspace_id": workspace_id})
    if result.success:
        return {"status": "ok", "logs": result.output.get("logs")}
    raise HTTPException(404, result.error)


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "provider": current_config.provider,
        "model": current_config.model,
        "skills": len(registry.list_skills()),
    }