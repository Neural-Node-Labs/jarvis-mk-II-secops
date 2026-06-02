"""
FastAPI Backend — Agent API Server
Endpoints: WebSocket /ws/chat, POST /api/confirm, GET /api/skills, POST /api/config, POST /api/reset
Evolution: /api/evolution/*, /ws/evolution
"""
import json
import logging
import os
import asyncio
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
from skills.self_evolution_skill.evolution_skill import (
    SelfEvolutionSkill,
    _sessions,
    _orchestrators,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("main")

SETTINGS_FILE = Path(os.getenv("SETTINGS_PATH", "/app/data/settings.json"))

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
            "temperature": cfg.temperature, "max_tokens": cfg.max_tokens, "schema_format": cfg.schema_format,
        }, indent=2))
    except Exception:
        pass

# --- Global State ---
registry = SkillRegistry()
registry.register("filesystem", FileSystemSkill())
registry.register("os_execution", OSExecutionSkill())
registry.register("cbd_architect", CBDArchitectSkill())
registry.register("self_evolution", SelfEvolutionSkill())
load_all_skills(registry)

_persisted = _load_persisted_settings()
_default_provider = _persisted.get("provider", "deepseek")
_defaults = PROVIDER_DEFAULTS.get(_default_provider, PROVIDER_DEFAULTS["deepseek"])
_key_env = _defaults.get("key_env") or ""

MAX_TOKENS = 8000

current_config = LLMConfig(
    provider=_default_provider,
    model=_persisted.get("model") or _defaults.get("model", "deepseek-chat"),
    api_key=os.getenv(_key_env, ""),
    base_url=_persisted.get("base_url") or _defaults.get("base_url", ""),
    temperature=_persisted.get("temperature", 0.7),
    max_tokens=_persisted.get("max_tokens", MAX_TOKENS),
    schema_format=_persisted.get("schema_format"),
)

agent = Agent(current_config, registry)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Agent backend started — {current_config.provider}/{current_config.model}")
    logger.info(f"Skills: {[s['name'] for s in registry.list_skills()]}")
    yield
    logger.info("Agent backend shutting down")

app = FastAPI(title="AI Agent API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


# --- Models ---
class ConfigUpdate(BaseModel):
    provider: str
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = MAX_TOKENS
    schema_format: Optional[str] = None

class ConfirmRequest(BaseModel):
    confirm_id: str

class EvolutionStartRequest(BaseModel):
    target_skill: str = ""
    task_description: str = ""

class EvolutionActionRequest(BaseModel):
    workspace_id: Optional[str] = None

# --- WS Chat ---
@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
                if msg.get("type") == "chat":
                    async for ev in agent.chat_stream(msg.get("message", "")):
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

# --- WS Evolution ---
@app.websocket("/ws/evolution")
async def ws_evolution(ws: WebSocket):
    await ws.accept()
    logger.info("Evolution WS connected")
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
                if msg.get("type") == "subscribe":
                    ws_id = msg.get("workspace_id") or (list(_sessions.keys())[-1] if _sessions else None)
                    if ws_id and ws_id in _sessions:
                        session = _sessions[ws_id]
                        await ws.send_text(json.dumps({"type": "evolution_state", "data": session.to_dict(), "workspace_id": ws_id}))
                        last_phase = session.current_phase
                        while session.status.value in ("running", "awaiting_approval", "approved"):
                            await asyncio.sleep(0.5)
                            if session.current_phase != last_phase:
                                await ws.send_text(json.dumps({"type": "evolution_state", "data": session.to_dict(), "workspace_id": ws_id}))
                                last_phase = session.current_phase
                            if session.status.value in ("completed", "failed", "aborted"):
                                break
                        await ws.send_text(json.dumps({"type": "evolution_complete", "data": session.to_dict(), "workspace_id": ws_id}))
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        logger.info("Evolution WS disconnected")


# --- REST ---
@app.get("/api/skills")
async def list_skills():
    return {"skills": registry.list_skills()}

@app.post("/api/config")
async def update_config(config: ConfigUpdate):
    global current_config, agent
    defaults = PROVIDER_DEFAULTS.get(config.provider, PROVIDER_DEFAULTS["deepseek"])
    key_env = defaults.get("key_env") or ""
    new_config = LLMConfig(
        provider=config.provider, model=config.model or defaults.get("model", ""),
        api_key=config.api_key or os.getenv(key_env, ""),
        base_url=config.base_url or defaults.get("base_url", ""),
        temperature=config.temperature or 0.7, max_tokens=config.max_tokens or MAX_TOKENS,
        schema_format=config.schema_format,
    )
    current_config = new_config
    agent = Agent(current_config, registry)
    _save_settings(config)
    return {"status": "ok", "provider": config.provider, "model": new_config.model}



@app.get("/api/config")
async def get_config():
    from core.llm_router import SCHEMA_OPENAI, SCHEMA_ANTHROPIC
    return {"provider": current_config.provider, "model": current_config.model, "available_providers": list(PROVIDER_DEFAULTS.keys()), "key_configured": bool(current_config.api_key and not current_config.api_key.startswith("MISSING_"))}

@app.post("/api/reset")
async def reset_agent():
    agent.reset()
    return {"status": "ok"}

# --- Evolution REST ---
@app.post("/api/evolution/start")
async def evo_start(req: EvolutionStartRequest):
    skill = registry.get("self_evolution")
    if not skill:
        raise HTTPException(500, "Self-evolution skill not registered")
    result = await skill.execute("start", {"target_skill": req.target_skill, "task_description": req.task_description})
    if result.success:
        return {"status": "ok", "workspace_id": result.output.get("workspace_id"), "state": result.output.get("state")}
    raise HTTPException(500, result.error)

@app.get("/api/evolution/status")
async def evo_status(workspace_id: Optional[str] = None):
    skill = registry.get("self_evolution")
    result = await skill.execute("status", {"workspace_id": workspace_id})
    if result.success:
        return {"status": "ok", "state": result.output.get("state")}
    raise HTTPException(404, result.error)

@app.post("/api/evolution/approve")
async def evo_approve(req: EvolutionActionRequest):
    skill = registry.get("self_evolution")
    result = await skill.execute("approve", {"workspace_id": req.workspace_id})
    if result.success:
        return {"status": "ok", "approved": True, "state": result.output.get("state")}
    raise HTTPException(400, result.error)

@app.post("/api/evolution/revise")
async def evo_revise(req: EvolutionActionRequest):
    skill = registry.get("self_evolution")
    result = await skill.execute("revise", {"workspace_id": req.workspace_id})
    if result.success:
        return {"status": "ok", "revised": True, "state": result.output.get("state")}
    raise HTTPException(400, result.error)

@app.post("/api/evolution/reset")
async def evo_reset(req: EvolutionActionRequest):
    skill = registry.get("self_evolution")
    await skill.execute("reset", {"workspace_id": req.workspace_id})
    return {"status": "ok", "reset": True}

@app.get("/api/evolution/logs")
async def evo_logs(workspace_id: Optional[str] = None):
    skill = registry.get("self_evolution")
    result = await skill.execute("get_logs", {"workspace_id": workspace_id})
    if result.success:
        return {"status": "ok", "logs": result.output.get("logs")}
    raise HTTPException(404, result.error)

@app.get("/api/health")
async def health():
    return {"status": "ok", "provider": current_config.provider, "model": current_config.model, "skills": len(registry.list_skills())}
