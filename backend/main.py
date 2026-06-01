"""
FastAPI Backend — Agent API Server
Endpoints: WebSocket /ws/chat, POST /api/confirm, GET /api/skills, POST /api/config, POST /api/reset
"""
import json
import logging
import os
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from core.llm_router import LLMConfig, PROVIDER_DEFAULTS
from core.skill_registry import SkillRegistry
from skills import load_all_skills
from core.agent import Agent
from skills.filesystem_skill import FileSystemSkill
from skills.os_execution_skill import OSExecutionSkill
from skills.cbd_skill import CBDArchitectSkill

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")


SETTINGS_FILE = Path(os.getenv("SETTINGS_PATH", "/app/data/settings.json"))


def _load_persisted_settings() -> dict:
    """Load settings from disk. Returns {} if file missing or corrupt."""
    try:
        if SETTINGS_FILE.exists():
            data = json.loads(SETTINGS_FILE.read_text())
            logger.info(f"Loaded persisted settings from {SETTINGS_FILE}")
            return data
    except Exception as e:
        logger.warning(f"Could not load settings file: {e}")
    return {}


def _save_settings(cfg: "ConfigUpdate") -> None:
    """Persist settings to disk (never writes api_key to disk for security)."""
    try:
        payload = {
            "provider": cfg.provider,
            "model": cfg.model,
            "base_url": cfg.base_url,
            "temperature": cfg.temperature,
            "max_tokens": cfg.max_tokens,
            "schema_format": cfg.schema_format,
            # api_key intentionally excluded — must be re-entered or set via env var
        }
        SETTINGS_FILE.write_text(json.dumps(payload, indent=2))
    except Exception as e:
        logger.warning(f"Could not persist settings: {e}")

# ─── Global State ─────────────────────────────────────────────────────────────

registry = SkillRegistry()
registry.register("filesystem", FileSystemSkill())
registry.register("os_execution", OSExecutionSkill())
registry.register("cbd_architect", CBDArchitectSkill())
load_all_skills(registry)

# Load persisted settings (if any), fall back to DeepSeek default
_persisted = _load_persisted_settings()
_default_provider = _persisted.get("provider", "deepseek")
_defaults = PROVIDER_DEFAULTS.get(_default_provider, PROVIDER_DEFAULTS["deepseek"])
_key_env = _defaults.get("key_env") or ""

current_config = LLMConfig(
    provider=_default_provider,
    model=_persisted.get("model") or _defaults.get("model", "deepseek-chat"),
    api_key=os.getenv(_key_env, ""),
    base_url=_persisted.get("base_url") or _defaults.get("base_url", ""),
    temperature=_persisted.get("temperature", 0.7),
    max_tokens=_persisted.get("max_tokens", 4096),
    schema_format=_persisted.get("schema_format"),  # None = auto
)

agent = Agent(current_config, registry)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Agent backend started")
    logger.info(f"   Provider: {current_config.provider} / {current_config.model}")
    logger.info(f"   Skills: {[s['name'] for s in registry.list_skills()]}")
    yield
    logger.info("Agent backend shutting down")


app = FastAPI(title="AI Agent API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Models ───────────────────────────────────────────────────────────────────

class ConfigUpdate(BaseModel):
    provider: str
    model: Optional[str] = None
    api_key: Optional[str] = None       # never persisted to disk
    base_url: Optional[str] = None
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 4096
    # schema_format: explicit override ("openai" | "anthropic" | null = auto)
    # When null the backend derives it from provider automatically.
    schema_format: Optional[str] = None


class ConfirmRequest(BaseModel):
    confirm_id: str


# ─── WebSocket Chat ───────────────────────────────────────────────────────────

@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    await websocket.accept()
    logger.info("WebSocket connected")
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
                message_type = msg.get("type", "chat")

                if message_type == "chat":
                    user_message = msg.get("message", "")
                    async for event in agent.chat_stream(user_message):
                        await websocket.send_text(json.dumps(event))

                elif message_type == "confirm":
                    confirm_id = msg.get("confirm_id")
                    if confirm_id:
                        async for event in agent.confirm_action(confirm_id):
                            await websocket.send_text(json.dumps(event))
                    else:
                        await websocket.send_text(json.dumps({
                            "type": "error", "data": "confirm_id missing"
                        }))

                elif message_type == "cancel_confirm":
                    confirm_id = msg.get("confirm_id")
                    if confirm_id and confirm_id in agent.pending_confirms:
                        agent.pending_confirms.pop(confirm_id)
                    await websocket.send_text(json.dumps({"type": "confirm_cancelled", "data": confirm_id}))

            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({"type": "error", "data": "Invalid JSON"}))

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")


# ─── REST Endpoints ───────────────────────────────────────────────────────────

@app.get("/api/skills")
async def list_skills():
    return {"skills": registry.list_skills()}


@app.post("/api/config")
async def update_config(config: ConfigUpdate):
    global current_config, agent
    defaults = PROVIDER_DEFAULTS.get(config.provider, PROVIDER_DEFAULTS["deepseek"])
    key_env = defaults.get("key_env") or ""

    # Resolve API key: explicit from UI > env var > empty (will surface as MISSING_ error)
    resolved_key = config.api_key or os.getenv(key_env, "")

    new_config = LLMConfig(
        provider=config.provider,
        model=config.model or defaults.get("model", ""),
        api_key=resolved_key,
        base_url=config.base_url or defaults.get("base_url", ""),
        temperature=config.temperature if config.temperature is not None else 0.7,
        max_tokens=config.max_tokens if config.max_tokens is not None else 4096,
        schema_format=config.schema_format,  # None = auto-derive in LLMRouter
    )
    current_config = new_config
    agent = Agent(current_config, registry)
    _save_settings(config)  # persist (without api_key)

    active_schema = new_config.get_schema()
    logger.info(f"Config updated: {config.provider}/{new_config.model} schema={active_schema}")
    return {
        "status": "ok",
        "provider": config.provider,
        "model": new_config.model,
        "schema_format": active_schema,
        "schema_override": config.schema_format is not None,
    }


@app.get("/api/config")
async def get_config():
    from core.llm_router import SCHEMA_OPENAI, SCHEMA_ANTHROPIC
    return {
        "provider": current_config.provider,
        "model": current_config.model,
        "base_url": current_config.base_url,
        "temperature": current_config.temperature,
        "max_tokens": current_config.max_tokens,
        # schema_format: the wire format currently active
        "schema_format": current_config.get_schema(),
        # schema_override: true if user manually forced a schema (not auto-derived)
        "schema_override": current_config.schema_format is not None,
        "available_providers": list(PROVIDER_DEFAULTS.keys()),
        "available_schemas": [SCHEMA_OPENAI, SCHEMA_ANTHROPIC],
        # key_configured: true if an API key is set (doesn't reveal the key)
        "key_configured": bool(
            current_config.api_key and
            not current_config.api_key.startswith("MISSING_")
        ),
    }


@app.post("/api/reset")
async def reset_agent():
    agent.reset()
    return {"status": "ok", "message": "Conversation cleared"}


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "provider": current_config.provider,
        "model": current_config.model,
        "skills": len(registry.list_skills()),
    }