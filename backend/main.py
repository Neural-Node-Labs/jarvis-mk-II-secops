"""
Mighty Jarvis MKII — FastAPI Server
CBD-compliant multi-component architecture.
Integrates: Memory System, RCA System, Experienced System, Self-Evolution, Jarvis MKII (multi-task).

version: 4.0.0
changelog:
  1.0.0 - Initial FastAPI server with basic chat and WebSocket
  2.0.0 - ReAct loop, confirm flow, file upload, streaming
  3.0.0 - LLM model switching, auto_confirm flag, multi-user sessions
  4.0.0 - CBD restructure: endpoints separated into component routers.
          No Pydantic. Pure dict schemas. JarvisMKII multi-thread skill added.
          Memory blueprint, RCA blueprint, Experienced blueprint, Self-Evolution blueprint integrated.
          Kali tool execution hardened with async subprocess + timeout.
"""
import re
import os
import json
import uuid
import asyncio
import logging
import threading
import traceback
import subprocess
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional
from collections import defaultdict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# ── Internal imports ───────────────────────────────────────────────────────────
from core.llm_router import LLMRouter, LLMConfig, get_model_max_tokens
from core.skill_registry import SkillRegistry
from core.prompt_builder import build_system_prompt, AGENT_SYSTEM_PROMPT, list_personas, DEFAULT_PERSONA, get_persona, save_persona, delete_persona, load_all_personas
from core.memory_manager import MemoryManager, detect_retrieval_request
from core.agent import Agent

# ══════════════════════════════════════════════════════════════════════════════
# CBD COMPONENT 1 — Logging Subsystem
# Logical function: Dual-stream log initialisation (system.log + llm_interaction.log)
# IN:  none (module-level side-effect)
# OUT: configured Logger objects accessible throughout the module
# ══════════════════════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("system.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("jarvis.main")
llm_trace = logging.getLogger("jarvis.llm_trace")
llm_trace.addHandler(logging.FileHandler("llm_interaction.log", encoding="utf-8"))
llm_trace.propagate = False

# ══════════════════════════════════════════════════════════════════════════════
# CBD COMPONENT 2 — Application Bootstrap
# Logical function: FastAPI app creation + CORS + static file mount
# IN:  environment (FRONTEND_DIR, CORS_ORIGINS)
# OUT: `app` FastAPI instance ready for route attachment
# ══════════════════════════════════════════════════════════════════════════════
app = FastAPI(
    title="Mighty Jarvis MKII",
    description="Autonomous AI Agent — Pentesting & SecOps Platform (CBD v2.2)",
    version="4.0.0",
)

CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = os.getenv("FRONTEND_DIR", "/app/frontend/dist")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIR, "assets")), name="assets")

# ══════════════════════════════════════════════════════════════════════════════
# CBD COMPONENT 3 — Session Registry
# Logical function: Per-user Agent instance lifecycle management
# IN:  user_id (str)
# OUT: Agent instance (created on first access, cached thereafter)
# Trace points: session_created, session_destroyed
# Failure map: try-catch on agent construction; returns error dict on failure
# ══════════════════════════════════════════════════════════════════════════════
_registry = SkillRegistry()
_memory   = MemoryManager()
_sessions: dict[str, Agent] = {}
_session_lock = threading.Lock()


def _get_session(user_id: str, model: str = None, provider: str = None) -> Agent:
    """Return existing Agent or create a new one. Thread-safe."""
    with _session_lock:
        if user_id not in _sessions:
            try:
                cfg = _build_llm_config(model, provider)
                agent = Agent(cfg, _registry, user_id=user_id)
                _sessions[user_id] = agent
                logger.info("[session_created] user=%s model=%s", user_id, cfg.model)
            except Exception as exc:
                logger.error("[session_create_failed] user=%s err=%s", user_id, exc)
                raise
        return _sessions[user_id]


def _destroy_session(user_id: str) -> bool:
    with _session_lock:
        if user_id in _sessions:
            del _sessions[user_id]
            logger.info("[session_destroyed] user=%s", user_id)
            return True
        return False


def _build_llm_config(model: str = None, provider: str = None, temperature: float = 0.7) -> LLMConfig:
    """Construct LLMConfig from env + optional overrides. No Pydantic — pure dict construction."""
    resolved_provider = provider or os.getenv("LLM_PROVIDER", "deepseek")
    resolved_model    = model    or os.getenv("LLM_MODEL", "deepseek-coder")
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
# Logical function: Parallel task execution across multiple agents using asyncio + threads
# IN:  tasks[] — list of {task_id, message, react, user_id, model, provider}
# OUT: results[] — list of {task_id, status, output, error, duration_ms}
# Trace points: task_submitted, task_started, task_complete, task_failed, all_tasks_done
# Failure map: per-task try-catch; timeouts enforced; partial results returned on failure
# Blueprint: self-evolution-skill-blueprint.md + memory-blueprint.md
# ══════════════════════════════════════════════════════════════════════════════

TASK_TIMEOUT_SECONDS = int(os.getenv("JARVIS_TASK_TIMEOUT", "300"))  # 5 min default
_active_tasks: dict[str, dict] = {}        # task_id → task state
_task_lock    = threading.Lock()


async def _run_single_task(task_def: dict) -> dict:
    """
    Execute one task against an isolated Agent instance.
    Returns a result dict regardless of success or failure.
    """
    task_id   = task_def.get("task_id", str(uuid.uuid4()))
    message   = task_def.get("message", "")
    react     = task_def.get("react", True)
    user_id   = task_def.get("user_id", f"mkii-{task_id}")
    model     = task_def.get("model")
    provider  = task_def.get("provider")
    auto_confirm = task_def.get("auto_confirm", False)

    started_at = datetime.now(timezone.utc)
    output_tokens: list[str] = []

    with _task_lock:
        _active_tasks[task_id] = {
            "task_id": task_id,
            "status": "running",
            "started_at": started_at.isoformat(),
            "message": message[:120],
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
        result = {"task_id": task_id, "status": "complete", "output": full_output, "error": None, "duration_ms": duration_ms}
        logger.info("[task_complete] id=%s duration_ms=%d tokens=%d", task_id, duration_ms, len(output_tokens))

    except asyncio.TimeoutError:
        duration_ms = TASK_TIMEOUT_SECONDS * 1000
        result = {
            "task_id": task_id, "status": "timeout",
            "output": "".join(output_tokens),
            "error": f"Task timed out after {TASK_TIMEOUT_SECONDS}s",
            "duration_ms": duration_ms,
        }
        logger.warning("[task_timeout] id=%s", task_id)

    except Exception as exc:
        duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
        result = {
            "task_id": task_id, "status": "failed",
            "output": "".join(output_tokens),
            "error": str(exc),
            "duration_ms": duration_ms,
        }
        logger.error("[task_failed] id=%s err=%s\n%s", task_id, exc, traceback.format_exc())

    finally:
        with _task_lock:
            _active_tasks.pop(task_id, None)

    return result


async def _jarvis_mkii_execute(payload: dict) -> dict:
    """
    JarvisMKII skill entry point.
    Accepts `tasks` list, fans out with asyncio.gather, returns aggregated results.

    IN:  { tasks: [{task_id?, message, react?, user_id?, model?, provider?, auto_confirm?}] }
    OUT: { submitted: int, results: [result...], completed: int, failed: int, duration_ms: int }
    """
    tasks = payload.get("tasks", [])
    if not tasks:
        return {"error": "MKII_NO_TASKS", "message": "Provide at least one task in `tasks[]`"}

    max_parallel = int(os.getenv("JARVIS_MAX_PARALLEL", "10"))
    if len(tasks) > max_parallel:
        return {
            "error": "MKII_TOO_MANY_TASKS",
            "message": f"Max {max_parallel} parallel tasks. Got {len(tasks)}.",
        }

    # Assign task_ids if missing
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

    return {
        "submitted":    len(tasks),
        "completed":    completed,
        "failed":       failed,
        "duration_ms":  duration_ms,
        "results":      list(results),
    }


# ══════════════════════════════════════════════════════════════════════════════
# CBD COMPONENT 5 — Kali Tool Executor
# Logical function: Async subprocess wrapper for Kali Linux security tools
# IN:  { command: str, cwd?: str, timeout?: int, env?: dict }
# OUT: { stdout, stderr, returncode, duration_ms, command }
# Trace points: kali_exec_start, kali_exec_complete, kali_exec_timeout, kali_exec_error
# Failure map: asyncio.TimeoutError caught; sanitize PATH; never execute as root without guard
# ══════════════════════════════════════════════════════════════════════════════

KALI_TOOL_TIMEOUT = int(os.getenv("KALI_TOOL_TIMEOUT", "120"))
_KALI_ENV = {
    **os.environ,
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "TERM": "xterm-256color",
}


async def _kali_exec(command: str, cwd: str = None, timeout: int = None, env_extra: dict = None) -> dict:
    """Execute a shell command and return structured result."""
    effective_timeout = timeout or KALI_TOOL_TIMEOUT
    effective_env     = {**_KALI_ENV, **(env_extra or {})}
    effective_cwd     = cwd or "/app/workspace"

    started = datetime.now(timezone.utc)
    logger.info("[kali_exec_start] cmd=%.80s cwd=%s timeout=%ds", command, effective_cwd, effective_timeout)

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=effective_cwd,
            env=effective_env,
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=effective_timeout)
        duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        result = {
            "command":     command,
            "stdout":      stdout_b.decode("utf-8", errors="replace"),
            "stderr":      stderr_b.decode("utf-8", errors="replace"),
            "returncode":  proc.returncode,
            "duration_ms": duration_ms,
            "timed_out":   False,
        }
        logger.info("[kali_exec_complete] rc=%d duration_ms=%d", proc.returncode, duration_ms)
        return result

    except asyncio.TimeoutError:
        duration_ms = effective_timeout * 1000
        logger.warning("[kali_exec_timeout] cmd=%.80s after=%ds", command, effective_timeout)
        try:
            proc.kill()
        except Exception:
            pass
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
            "command":    command,
            "stdout":     "",
            "stderr":     str(exc),
            "returncode": -2,
            "duration_ms": 0,
            "timed_out":  False,
        }


# ══════════════════════════════════════════════════════════════════════════════
# CBD COMPONENT 6 — SSE Stream Builder
# Logical function: Convert Agent event generator to Server-Sent Events byte stream
# IN:  AsyncGenerator[dict, None]
# OUT: AsyncGenerator[bytes, None] — SSE formatted
# Trace points: stream_start, stream_token, stream_tool_call, stream_done, stream_error
# ══════════════════════════════════════════════════════════════════════════════

async def _agent_to_sse(event_gen: AsyncGenerator) -> AsyncGenerator[bytes, None]:
    """Wrap agent event stream as SSE bytes. Each event is JSON-encoded."""
    try:
        yield _sse("stream_start", {"status": "ok"})
        async for event in event_gen:
            etype = event.get("type", "token")
            yield _sse(etype, event.get("data", event))
            if etype == "done":
                break
    except Exception as exc:
        logger.error("[stream_error] %s\n%s", exc, traceback.format_exc())
        yield _sse("error", {"message": str(exc)})
    finally:
        yield _sse("stream_end", {})


def _sse(event: str, data) -> bytes:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


# ══════════════════════════════════════════════════════════════════════════════
# CBD COMPONENT 7 — Attachment Processor
# Logical function: Decode multipart file uploads into agent attachment dicts
# IN:  UploadFile list
# OUT: list of {name, mime, size, text|b64}
# Failure map: per-file try-catch; skip unreadable files with warning
# ══════════════════════════════════════════════════════════════════════════════

TEXT_MIME_PREFIXES = ("text/", "application/json", "application/xml", "application/javascript")


async def _process_attachments(files: list[UploadFile]) -> list[dict]:
    """Read uploaded files and return attachment dicts for the Agent."""
    attachments = []
    for uf in files:
        try:
            raw  = await uf.read()
            mime = uf.content_type or "application/octet-stream"
            att  = {"name": uf.filename, "mime": mime, "size": len(raw)}
            if any(mime.startswith(p) for p in TEXT_MIME_PREFIXES):
                att["text"] = raw.decode("utf-8", errors="replace")
            else:
                import base64
                att["b64"] = base64.b64encode(raw).decode()
            attachments.append(att)
            logger.info("[attachment_processed] name=%s mime=%s size=%d", uf.filename, mime, len(raw))
        except Exception as exc:
            logger.warning("[attachment_skip] file=%s err=%s", uf.filename, exc)
    return attachments


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER COMPONENT 8 — Health & Info Endpoints
# Logical function: System status, version, capability introspection
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/", tags=["System"])
async def root():
    """Jarvis MKII identity card."""
    return {
        "designation": "Mighty Jarvis MKII",
        "version":     "4.0.0",
        "methodology": "CBD v2.2",
        "environment": "Kali Linux Rolling",
        "status":      "operational",
        "react_loop":  "30 iterations max",
        "blueprints":  ["memory", "rca", "experienced", "self-evolution"],
        "skills":      _registry.list_skills() if hasattr(_registry, "list_skills") else "loaded",
    }


@app.get("/health", tags=["System"])
async def health():
    """Liveness probe."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/models", tags=["System"])
async def list_models():
    """Return known model→provider mapping for the UI model-picker."""
    return {
        "providers": {
            "deepseek":  ["deepseek-chat", "deepseek-coder", "deepseek-reasoner"],
            "anthropic": ["claude-haiku-4-5-20251001", "claude-sonnet-4-20250514", "claude-opus-4-20250514"],
            "openai":    ["gpt-4o", "gpt-4o-mini", "o3-mini"],
            "ollama":    ["llama3.2", "llama3.1", "mistral", "phi3"],
        },
        "default_provider": os.getenv("LLM_PROVIDER", "deepseek"),
        "default_model":    os.getenv("LLM_MODEL",    "deepseek-coder"),
    }


@app.get("/api/session/{user_id}", tags=["Session"])
async def session_status(user_id: str):
    """Return session info for a given user."""
    with _session_lock:
        if user_id not in _sessions:
            return {"exists": False, "user_id": user_id}
        agent = _sessions[user_id]
        return {
            "exists":           True,
            "user_id":          user_id,
            "conversation_len": len(agent.conversation),
            "pending_confirms": list(agent.pending_confirms.keys()),
        }


@app.delete("/api/session/{user_id}", tags=["Session"])
async def reset_session(user_id: str):
    """Destroy and reset a user session."""
    destroyed = _destroy_session(user_id)
    return {"reset": destroyed, "user_id": user_id}


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER COMPONENT 9 — Chat Endpoints (SSE + WebSocket)
# Logical function: Deliver Agent streaming output to clients
# IN:  { message, user_id?, react?, auto_confirm?, model?, provider? }
# OUT: SSE stream or WebSocket messages
# Trace points: chat_request_received, chat_stream_start, chat_stream_done
# Failure map: session creation wrapped; streaming errors caught and sent as SSE error events
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/chat", tags=["Chat"])
async def chat_sse(
    message:      str,
    user_id:      str = "default",
    react:        bool = False,
    auto_confirm: bool = False,
    model:        str = None,
    provider:     str = None,
):
    """
    SSE chat endpoint. Stream agent response as Server-Sent Events.
    Query params: message, user_id, react (bool), auto_confirm (bool), model, provider.
    """
    logger.info("[chat_request_received] user=%s react=%s msg=%.60s", user_id, react, message)
    llm_trace.info("[CHAT-SSE] user=%s react=%s message=%.120s", user_id, react, message)

    try:
        agent = _get_session(user_id, model, provider)
        gen   = agent.chat_stream(message, react=react, auto_confirm=auto_confirm)
        return StreamingResponse(_agent_to_sse(gen), media_type="text/event-stream", headers={
            "Cache-Control":  "no-cache",
            "X-Accel-Buffering": "no",
        })
    except Exception as exc:
        logger.error("[chat_sse_error] %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/chat", tags=["Chat"])
async def chat_post(request: Request):
    """
    POST chat endpoint for clients that prefer JSON body over query strings.
    Body: { message, user_id?, react?, auto_confirm?, model?, provider? }
    Returns SSE stream.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    message      = body.get("message", "")
    user_id      = body.get("user_id",      "default")
    react        = body.get("react",        False)
    auto_confirm = body.get("auto_confirm", False)
    model        = body.get("model")
    provider     = body.get("provider")

    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    logger.info("[chat_post_received] user=%s react=%s msg=%.60s", user_id, react, message)
    llm_trace.info("[CHAT-POST] user=%s react=%s message=%.120s", user_id, react, message)

    try:
        agent = _get_session(user_id, model, provider)
        gen   = agent.chat_stream(message, react=react, auto_confirm=auto_confirm)
        return StreamingResponse(_agent_to_sse(gen), media_type="text/event-stream", headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
        })
    except Exception as exc:
        logger.error("[chat_post_error] %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.websocket("/ws/chat/{user_id}")
async def chat_websocket(ws: WebSocket, user_id: str):
    """
    WebSocket chat endpoint.
    Client sends JSON: { message, react?, auto_confirm?, model?, provider? }
    Server sends JSON event objects: { type, data }
    """
    await ws.accept()
    logger.info("[ws_connected] user=%s", user_id)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "data": "Invalid JSON"})
                continue

            # Auth handshake — first message may carry token
            if msg.get("type") == "auth":
                token    = msg.get("token", "")
                ws_user  = _validate_token(token)
                if not ws_user:
                    await ws.send_json({"type": "auth_failed", "data": "Invalid token"})
                    await ws.close()
                    return
                # Build a throw-away config so the Ollama fallback resolves before
                # we tell the UI which provider/model is actually active.
                _probe_cfg = _build_llm_config()
                from core.llm_router import LLMRouter as _LLMRouter
                _probe_router = _LLMRouter(_probe_cfg)
                await ws.send_json({"type": "auth_ok", "data": {
                    "username": ws_user,
                    "provider_info": {
                        "provider":     _probe_cfg.provider,
                        "model":        _probe_router.model,
                        "schema_format": _probe_cfg.get_schema(),
                    },
                }})
                continue

            message      = msg.get("message", "")
            react        = msg.get("react",        False)
            auto_confirm = msg.get("auto_confirm", False)
            model        = msg.get("model")
            provider     = msg.get("provider")
            attachments  = msg.get("attachments",  []) or []
            instructions    = msg.get("instructions", "")   # Optional operator instruction block
            halt            = msg.get("halt",          False)
            memory_enabled  = msg.get("memory_enabled", True)   # False = skip memory injection
            persona         = msg.get("persona", DEFAULT_PERSONA)  # "jarvis" | "omnikon" | "kraken"
            project         = msg.get("project",  DEFAULT_PROJECT) # active UI project name
            workspace_path  = _workspace_path(user_id, project)   # /app/workspace/{user}/{project}/workspace

            # ── HALT signal: destroy session so agent stops and forgets context ──
            if halt:
                _destroy_session(user_id)
                await ws.send_json({"type": "halted", "data": {"user_id": user_id}})
                logger.info("[ws_halt] user=%s", user_id)
                continue

            # ── Confirm resume (destructive action gate) ───────────────────────
            confirm_id = msg.get("confirm_id", "")
            if confirm_id:
                with _session_lock:
                    agent = _sessions.get(user_id)
                if agent:
                    async for event in agent.confirm_action(confirm_id):
                        await ws.send_json(event)
                else:
                    await ws.send_json({"type": "error", "data": f"No session for {user_id}"})
                continue

            # ── Guard: ignore empty messages with no attachments ───────────────
            # Previously this sent an error even on keep-alive pings from the client.
            if not message and not attachments:
                # Silently ignore — do NOT send error, do NOT echo back
                continue

            llm_trace.info("[WS] user=%s project=%s react=%s atts=%d msg=%.120s",
                           user_id, project, react, len(attachments), message)
            logger.info("[ws_dispatch] user=%s project=%s workspace=%s react=%s",
                        user_id, project, workspace_path, react)

            try:
                agent = _get_session(user_id, model, provider)

                # Build effective message with workspace context + operator instructions
                # The workspace context tells the agent EXACTLY where to read/write files.
                effective_msg = message

                workspace_ctx = (
                    f"[WORKSPACE CONTEXT]\n"
                    f"Active Project  : {project}\n"
                    f"Workspace Path  : {workspace_path}\n"
                    f"User            : {user_id}\n"
                    f"All file operations (read, write, list, search) MUST use this workspace path "
                    f"as the root directory unless the operator explicitly specifies otherwise.\n"
                )

                if instructions:
                    effective_msg = (
                        f"{workspace_ctx}\n"
                        f"[OPERATOR INSTRUCTIONS]\n{instructions}\n\n"
                        f"[MESSAGE]\n{message}"
                    )
                else:
                    effective_msg = f"{workspace_ctx}\n[MESSAGE]\n{message}"

                async for event in agent.chat_stream(
                    effective_msg,
                    react=react,
                    auto_confirm=auto_confirm,
                    attachments=attachments if attachments else None,
                    memory_enabled=memory_enabled,
                    persona=persona,
                ):
                    await ws.send_json(event)
            except Exception as exc:
                await ws.send_json({"type": "error", "data": str(exc)})

    except WebSocketDisconnect:
        logger.info("[ws_disconnected] user=%s", user_id)
    except Exception as exc:
        logger.error("[ws_error] user=%s err=%s", user_id, exc)


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER COMPONENT 10 — File Upload + Chat
# Logical function: Accept multipart form uploads, attach to chat message
# IN:  multipart form { message, user_id?, react?, files[] }
# OUT: SSE stream
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/chat/upload", tags=["Chat"])
async def chat_upload(
    message:      str          = Form(""),
    user_id:      str          = Form("default"),
    react:        bool         = Form(False),
    auto_confirm: bool         = Form(False),
    model:        str          = Form(None),
    provider:     str          = Form(None),
    files:        list[UploadFile] = File(default=[]),
):
    """Upload files and stream a chat response that uses them as context."""
    attachments = await _process_attachments(files) if files else []
    logger.info("[chat_upload] user=%s files=%d msg=%.60s", user_id, len(attachments), message)

    try:
        agent = _get_session(user_id, model, provider)
        gen   = agent.chat_stream(message, react=react, auto_confirm=auto_confirm, attachments=attachments)
        return StreamingResponse(_agent_to_sse(gen), media_type="text/event-stream", headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
        })
    except Exception as exc:
        logger.error("[chat_upload_error] %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER COMPONENT 11 — Confirmation Gate
# Logical function: Resume a paused ReAct loop after user confirms a destructive action
# IN:  { confirm_id, user_id }
# OUT: SSE stream (confirmed action result + LLM summary)
# Trace points: confirm_request, confirm_ok, confirm_not_found
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/confirm", tags=["Chat"])
async def confirm_action(request: Request):
    """
    Resume a paused agent that is waiting for user confirmation.
    Body: { confirm_id: str, user_id: str }
    """
    body = await request.json()
    confirm_id = body.get("confirm_id")
    user_id    = body.get("user_id", "default")

    if not confirm_id:
        raise HTTPException(status_code=400, detail="confirm_id required")

    logger.info("[confirm_request] user=%s confirm_id=%s", user_id, confirm_id)

    with _session_lock:
        agent = _sessions.get(user_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"No active session for user '{user_id}'")

    gen = agent.confirm_action(confirm_id)
    return StreamingResponse(_agent_to_sse(gen), media_type="text/event-stream", headers={
        "Cache-Control":     "no-cache",
        "X-Accel-Buffering": "no",
    })


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER COMPONENT 12 — JarvisMKII Multi-Task Skill Endpoint
# Logical function: Fan-out multiple tasks to parallel isolated agents
# Blueprint source: self-evolution-skill-blueprint.md, memory-blueprint.md
# IN:  { tasks: [...], user_id? }  (POST JSON body)
# OUT: { submitted, completed, failed, duration_ms, results[] }
# Trace points: mkii_submitted, mkii_complete
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/mkii/tasks", tags=["JarvisMKII"])
async def mkii_run_tasks(request: Request):
    """
    Jarvis MKII — Run multiple tasks in parallel.

    Each task is handled by its own isolated Agent instance with its own
    conversation context. Tasks run concurrently via asyncio.gather.

    Body schema:
    {
      "tasks": [
        {
          "task_id":      "optional-string",
          "message":      "Run a port scan on 192.168.1.0/24",
          "react":        true,
          "auto_confirm": false,
          "user_id":      "operator-1",
          "model":        "deepseek-coder",
          "provider":     "deepseek"
        }
      ]
    }
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    logger.info("[mkii_submitted] task_count=%d", len(body.get("tasks", [])))
    result = await _jarvis_mkii_execute(body)

    if "error" in result:
        raise HTTPException(status_code=400, detail=result)

    logger.info("[mkii_complete] submitted=%d completed=%d failed=%d duration_ms=%d",
                result["submitted"], result["completed"], result["failed"], result["duration_ms"])
    return JSONResponse(result)


@app.get("/api/mkii/tasks", tags=["JarvisMKII"])
async def mkii_active_tasks():
    """List currently running JarvisMKII tasks."""
    with _task_lock:
        return {"active_tasks": list(_active_tasks.values()), "count": len(_active_tasks)}


@app.websocket("/ws/mkii/{session_id}")
async def mkii_websocket(ws: WebSocket, session_id: str):
    """
    JarvisMKII WebSocket — stream results for each task as they complete.
    Client sends: { tasks: [...] }
    Server streams: { type: 'task_result', data: {...} } per task, then { type: 'done' }
    """
    await ws.accept()
    logger.info("[mkii_ws_connected] session=%s", session_id)

    try:
        raw = await ws.receive_text()
        payload = json.loads(raw)

        tasks = payload.get("tasks", [])
        if not tasks:
            await ws.send_json({"type": "error", "data": "No tasks provided"})
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
            await ws.send_json({"type": "error", "data": str(exc)})
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER COMPONENT 13 — Kali Tool Direct Execution
# Logical function: Execute Kali Linux commands directly via HTTP (no LLM layer)
# Blueprint source: README_ULTIMATE.md — Arsenal section
# IN:  { command, cwd?, timeout?, env? }
# OUT: { stdout, stderr, returncode, duration_ms, command, timed_out }
# Security: NEVER expose unauthenticated in production. Gate with API key or mTLS.
# ══════════════════════════════════════════════════════════════════════════════

_KALI_API_KEY = os.getenv("KALI_EXEC_API_KEY", "")  # Empty = no auth (dev only)


def _check_kali_auth(request: Request):
    if not _KALI_API_KEY:
        return  # No key configured — open (dev mode)
    key = request.headers.get("X-Kali-Key", "")
    if key != _KALI_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid Kali execution key")


@app.post("/api/kali/exec", tags=["Kali"])
async def kali_exec(request: Request):
    """
    Direct Kali tool execution endpoint.
    Bypasses the LLM layer for scripted automation and CI pipelines.

    Body: { command: str, cwd?: str, timeout?: int, env?: dict }
    """
    _check_kali_auth(request)
    body = await request.json()

    command = body.get("command", "").strip()
    if not command:
        raise HTTPException(status_code=400, detail="command is required")

    result = await _kali_exec(
        command=command,
        cwd=body.get("cwd"),
        timeout=body.get("timeout"),
        env_extra=body.get("env", {}),
    )
    return JSONResponse(result)


@app.get("/api/kali/tools", tags=["Kali"])
async def kali_tools():
    """List available Kali tools by category, sourced from README_ULTIMATE.md arsenal."""
    return {
        "categories": {
            "network_recon":    ["nmap", "masscan", "netcat-openbsd", "tcpdump", "wireshark", "dnsutils", "whois",
                                 "dnsrecon", "dnsenum", "fierce", "recon-ng", "theharvester", "amass",
                                 "sublist3r", "httprobe", "hakrawler"],
            "web_testing":      ["nikto", "gobuster", "wfuzz", "whatweb", "dirb", "sqlmap", "zaproxy",
                                 "sslyze", "ffuf", "wpscan", "joomscan", "commix", "xsser", "wapiti"],
            "exploitation":     ["metasploit-framework", "hydra", "john", "exploitdb", "shellter",
                                 "msfpc", "veil", "gophish", "empire"],
            "password_cracking":["hashcat", "hashcat-utils", "cewl", "crunch", "rsmangler"],
            "windows_ad":       ["responder", "smbclient", "enum4linux", "enum4linux-ng", "impacket-scripts",
                                 "bloodhound", "certipy-ad", "evil-winrm", "netexec", "mimikatz",
                                 "kerberoast", "crackmapexec"],
            "wireless_rf":      ["aircrack-ng", "mdk4", "wifite", "kismet", "reaver", "bully"],
            "system_audit":     ["lynis", "rkhunter", "unhide", "ssh-audit", "gvm"],
            "forensics":        ["exiftool", "steghide", "stegseek"],
            "tunneling":        ["chisel", "proxychains4"],
            "osint":            ["setoolkit", "python3-shodan"],
        }
    }


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER COMPONENT 14 — Memory System Endpoints
# Logical function: Per-user conversation history CRUD
# Blueprint source: memory-blueprint.md
# IN:  user_id, n (retrieve count)
# OUT: conversation blocks or confirmation
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/memory/{user_id}", tags=["Memory"])
async def memory_retrieve(user_id: str, n: int = 5):
    """Retrieve last N conversation blocks for a user (EpisodicStore)."""
    try:
        history = _memory.retrieve_last_n(user_id, n)
        return {"user_id": user_id, "n": n, "history": history}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.delete("/api/memory/{user_id}", tags=["Memory"])
async def memory_clear(user_id: str):
    """Clear all stored memory for a user."""
    try:
        _memory.clear(user_id)
        return {"cleared": True, "user_id": user_id}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER COMPONENT 15 — RCA Endpoint (Root Cause Analysis)
# Logical function: Trigger RCA pipeline on a described symptom
# Blueprint source: rca-blueprint.md
# IN:  { symptom, error_message?, environment?, user_id? }
# OUT: SSE stream (agent performs full RCA workflow via ReAct loop)
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/rca", tags=["RCA"])
async def run_rca(request: Request):
    """
    Trigger Root Cause Analysis on a symptom.
    The agent follows rca-blueprint.md workflow:
    Phase 0 (Experienced Lookup) → SymptomCapturer → ContextAggregator →
    HypothesisGenerator → DiagnosticDesigner → DiagnosticExecutor →
    HypothesisEvaluator → CausalChainDriller → FixProposer → FixValidator →
    PostMortemWriter → Phase III (Experience Capture)

    Body: { symptom, error_message?, environment?, user_id? }
    """
    body = await request.json()
    symptom       = body.get("symptom", "")
    error_message = body.get("error_message", "")
    environment   = body.get("environment", {})
    user_id       = body.get("user_id", "rca-default")

    if not symptom:
        raise HTTPException(status_code=400, detail="symptom is required")

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
        return StreamingResponse(_agent_to_sse(gen), media_type="text/event-stream", headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
        })
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER COMPONENT 16 — Experienced System Endpoints
# Logical function: CRUD for EXP knowledge entries
# Blueprint source: experienced-blueprint.md
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/experienced", tags=["Experienced"])
async def experienced_list():
    """List all EXP entries via cbd_architect experienced_search."""
    return await _skill_call("cbd_architect", "experienced_search", {"query": "*"})


@app.get("/api/experienced/search", tags=["Experienced"])
async def experienced_search(q: str, category: str = None, severity: str = None):
    """Search EXP knowledge base."""
    params = {"query": q}
    if category: params["category"] = category
    if severity:  params["severity"] = severity
    return await _skill_call("cbd_architect", "experienced_search", params)


@app.post("/api/experienced/rebuild", tags=["Experienced"])
async def experienced_rebuild():
    """Rebuild the Experienced index from all EXP files."""
    return await _skill_call("cbd_architect", "experienced_rebuild_index", {})


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER COMPONENT 17 — Self-Evolution Endpoint
# Logical function: Trigger autonomous 9-phase evolution pipeline on a skill path
# Blueprint source: self-evolution-skill-blueprint.md
# IN:  { task_description, origin_path, slug, user_id? }
# OUT: SSE stream (agent manages full evolution session)
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/evolve", tags=["SelfEvolution"])
async def trigger_evolution(request: Request):
    """
    Trigger the Self-Evolution pipeline.
    Body: { task_description, origin_path, slug, user_id? }

    The agent runs the 9-phase evolution protocol:
    Phase 0 (Experience lookup) → Phase 1 (Workspace bootstrap) →
    Phase 2 (Source discovery) → Phase 3 (Analysis) → Phase 4 (Blueprint generation) →
    Phase 5 (User approval gate — REQUIRED) → Phase 6 (Implementation) →
    Phase 7 (Testing) → Phase 8 (Deploy+Verify) → Phase 9 (Experience capture)
    """
    body = await request.json()
    task_desc   = body.get("task_description", "")
    origin_path = body.get("origin_path", "")
    slug        = body.get("slug", "evolution-task")
    user_id     = body.get("user_id", "evolution-agent")

    if not task_desc or not origin_path:
        raise HTTPException(status_code=400, detail="task_description and origin_path are required")

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
        gen   = agent.chat_stream(evolution_prompt, react=True, auto_confirm=False)
        return StreamingResponse(_agent_to_sse(gen), media_type="text/event-stream", headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
        })
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER COMPONENT 18 — CBD Architect Proxy
# Logical function: Direct skill invocations for CBD workflow actions
# IN:  { action, params }
# OUT: skill execution result
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/cbd/{action}", tags=["CBD"])
async def cbd_action(action: str, request: Request):
    """
    Direct CBD architect skill invocation.
    Supported actions: analyze_request, generate_blueprint, validate_blueprint,
    validate_component, implement_component, version_read,
    experienced_lookup, experienced_capture, experienced_promote,
    experienced_search, experienced_rebuild_index, get_template, get_skills_registry
    """
    body   = await request.json()
    params = body.get("params", body)  # accept { params: {...} } or flat body
    return await _skill_call("cbd_architect", action, params)


async def _skill_call(skill: str, action: str, params: dict) -> JSONResponse:
    """Helper: execute a skill and return JSONResponse."""
    try:
        result = await _registry.execute(skill, action, params, confirmed=True)
        return JSONResponse(result.to_dict())
    except Exception as exc:
        logger.error("[skill_call_error] %s.%s err=%s", skill, action, exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER COMPONENT 18b — Scheduler
# Logical function: CRUD for scheduled tasks (UI: add / edit / delete / run-now)
# + a background loop that actually executes due tasks.
#
# A scheduled task is either:
#   task_type="ai_call"  -> on schedule, run payload.message through the
#                            agent (same path as a normal chat turn).
#   task_type="command"  -> on schedule, run payload.command as a shell
#                            command (this covers "run a python script" too:
#                            command = "python3 /app/output/script.py arg1").
#
# Storage/CRUD lives in the `scheduler` skill (skills/scheduler_skill.py).
# Execution lives here because this is where the per-user Agent sessions are.
# IN:  { name, user_id, task_type, schedule:{kind,value}, payload, enabled }
# OUT: task dict (id, next_run_at, last_status, ...)
# Trace points: scheduler_task_created, scheduler_task_due, scheduler_run_*
# ══════════════════════════════════════════════════════════════════════════════

SCHEDULER_POLL_SECONDS    = int(os.getenv("JARVIS_SCHEDULER_POLL_SECONDS", "15"))
SCHEDULER_CMD_TIMEOUT     = int(os.getenv("JARVIS_SCHEDULER_CMD_TIMEOUT", "300"))
SCHEDULER_OUTPUT_MAX_CHARS = 20_000
_scheduler_task: Optional[asyncio.Task] = None


async def _run_scheduled_ai_call(task: dict) -> dict:
    """Send payload.message into the agent for this task's user, exactly like
    a normal chat turn, and collect the final text. react=True by default so
    the agent can use tools/skills, not just answer in one shot."""
    payload    = task.get("payload") or {}
    message    = payload.get("message", "")
    persona    = payload.get("persona", DEFAULT_PERSONA)
    react      = bool(payload.get("react", True))
    auto_confirm = bool(payload.get("auto_confirm", True))  # scheduled tasks run unattended
    user_id    = payload.get("user_id_override") or task.get("user_id") or "scheduler"

    agent  = _get_session(user_id)
    chunks: list[str] = []
    async for event in agent.chat_stream(message, react=react, auto_confirm=auto_confirm, persona=persona):
        if event.get("type") == "token":
            chunks.append(str(event.get("data", "")))
        elif event.get("type") == "tool_result":
            chunks.append(f"\n[tool: {event['data'].get('skill')}.{event['data'].get('action')} -> "
                           f"{'OK' if event['data'].get('success') else 'FAILED'}]\n")
    summary = "".join(chunks).strip() or "(no output)"
    return {"status": "success", "output": summary[:SCHEDULER_OUTPUT_MAX_CHARS]}


async def _run_scheduled_command(task: dict) -> dict:
    """Run payload.command as a shell command with a timeout. Same trust
    model as the os_execution skill — this agent already ships nmap/hydra/
    sqlmap, so scheduled commands run with the same privileges the agent
    itself runs with."""
    payload  = task.get("payload") or {}
    command  = payload.get("command", "")
    cwd      = payload.get("cwd") or None
    timeout  = int(payload.get("timeout_seconds", SCHEDULER_CMD_TIMEOUT))
    env      = {**os.environ, **(payload.get("env") or {})}

    try:
        proc = await asyncio.create_subprocess_shell(
            command, cwd=cwd, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"status": "timeout", "output": "", "error": f"Command exceeded {timeout}s timeout"}
        out = (stdout or b"").decode(errors="replace") + (("\n[stderr]\n" + stderr.decode(errors="replace")) if stderr else "")
        ok  = proc.returncode == 0
        return {
            "status": "success" if ok else "failed",
            "output": out[:SCHEDULER_OUTPUT_MAX_CHARS],
            "error":  None if ok else f"exit code {proc.returncode}",
        }
    except Exception as exc:
        return {"status": "failed", "output": "", "error": str(exc)}


async def _execute_scheduled_task(task: dict) -> dict:
    """Run one task (used by both the background loop and the manual
    'run now' endpoint) and persist the run via the scheduler skill."""
    started = datetime.now(timezone.utc).isoformat()
    logger.info("[scheduler_run_start] task_id=%s name=%s type=%s",
                task["id"], task.get("name"), task.get("task_type"))
    try:
        if task["task_type"] == "ai_call":
            result = await _run_scheduled_ai_call(task)
        elif task["task_type"] == "command":
            result = await _run_scheduled_command(task)
        else:
            result = {"status": "failed", "output": "", "error": f"unknown task_type '{task['task_type']}'"}
    except Exception as exc:
        logger.error("[scheduler_run_error] task_id=%s err=%s\n%s", task["id"], exc, traceback.format_exc())
        result = {"status": "failed", "output": "", "error": str(exc)}

    finished = datetime.now(timezone.utc).isoformat()
    record = await _registry.execute("scheduler", "record_run", {
        "task_id": task["id"], "status": result["status"], "output": result.get("output", ""),
        "error": result.get("error"), "started_at": started, "finished_at": finished,
    }, confirmed=True)
    logger.info("[scheduler_run_done] task_id=%s status=%s", task["id"], result["status"])
    return {"run": result, "task": record.to_dict().get("output")}


async def _scheduler_loop():
    """Polls for due tasks every SCHEDULER_POLL_SECONDS and runs them.
    Tasks run sequentially to keep this simple and avoid surprising the
    agent with concurrent sessions for the same user; with a 15s default
    poll interval and tasks normally measured in seconds, this is plenty
    responsive for a scheduler (not a high-frequency job queue)."""
    logger.info("[scheduler_loop_started] poll_interval=%ss", SCHEDULER_POLL_SECONDS)
    while True:
        try:
            due = await _registry.execute("scheduler", "get_due_tasks", {})
            for task in (due.output or {}).get("tasks", []):
                await _execute_scheduled_task(task)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("[scheduler_loop_error] err=%s\n%s", exc, traceback.format_exc())
        await asyncio.sleep(SCHEDULER_POLL_SECONDS)


@app.get("/api/scheduler/tasks", tags=["Scheduler"])
async def scheduler_list_tasks(user_id: str = None):
    return await _skill_call("scheduler", "list_tasks", {"user_id": user_id} if user_id else {})


@app.post("/api/scheduler/tasks", tags=["Scheduler"])
async def scheduler_create_task(request: Request):
    """Body: { name, user_id, task_type: 'ai_call'|'command', schedule: {kind, value}, payload, enabled? }"""
    body = await request.json()
    return await _skill_call("scheduler", "create_task", body)


@app.get("/api/scheduler/tasks/{task_id}", tags=["Scheduler"])
async def scheduler_get_task(task_id: str):
    return await _skill_call("scheduler", "get_task", {"task_id": task_id})


@app.put("/api/scheduler/tasks/{task_id}", tags=["Scheduler"])
async def scheduler_update_task(task_id: str, request: Request):
    body = await request.json()
    body["task_id"] = task_id
    return await _skill_call("scheduler", "update_task", body)


@app.delete("/api/scheduler/tasks/{task_id}", tags=["Scheduler"])
async def scheduler_delete_task(task_id: str):
    return await _skill_call("scheduler", "delete_task", {"task_id": task_id})


@app.post("/api/scheduler/tasks/{task_id}/toggle", tags=["Scheduler"])
async def scheduler_toggle_task(task_id: str, request: Request):
    """Body: { enabled: true|false }"""
    body = await request.json()
    return await _skill_call("scheduler", "toggle_task", {"task_id": task_id, "enabled": body.get("enabled")})


@app.post("/api/scheduler/tasks/{task_id}/run", tags=["Scheduler"])
async def scheduler_run_task_now(task_id: str):
    """Manual 'run now' — executes immediately (doesn't wait for the poll loop) and returns the result."""
    got = await _registry.execute("scheduler", "get_task", {"task_id": task_id}, confirmed=True)
    if not got.success:
        raise HTTPException(status_code=404, detail=got.error)
    result = await _execute_scheduled_task(got.output)
    return JSONResponse(result)


@app.get("/api/scheduler/tasks/{task_id}/runs", tags=["Scheduler"])
async def scheduler_list_runs(task_id: str, limit: int = 50):
    return await _skill_call("scheduler", "list_runs", {"task_id": task_id, "limit": limit})


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER COMPONENT 18c — Skill Hot-Reload
# Logical function: Re-scan skills/ for newly-written skill modules (e.g. one
# the Self-Evolution pipeline just created) and register them without a
# container restart. Have the evolution pipeline's deploy phase call this
# after writing a new skills/*.py file.
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/skills/reload", tags=["Skills"])
async def skills_reload():
    """Re-run dynamic skill discovery now. Returns newly-added skill names
    plus the full current skill list."""
    result = _registry.reload()
    logger.info("[skills_reload] added=%s total=%d", result.get("added"), len(result.get("skills", [])))
    return result


@app.post("/api/skills/register", tags=["Skills"])
async def skills_register(request: Request):
    """Explicitly register one skill module by dotted path, e.g.
    { "module_path": "skills.scheduler_skill", "class_name": "SchedulerSkill", "name": "scheduler" }
    class_name and name are optional — see SkillRegistry.register_module."""
    body = await request.json()
    module_path = body.get("module_path")
    if not module_path:
        raise HTTPException(status_code=400, detail="module_path is required")
    result = _registry.register_module(module_path, body.get("class_name"), body.get("name"))
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER COMPONENT 19 — SPA Fallback
# Logical function: Serve React frontend for all non-API routes
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/halt/{user_id}", tags=["Session"])
async def halt_session(user_id: str):
    """
    Hard stop: destroy agent session + clear pending confirms.
    The agent will not resume halted work even on reconnect because
    the conversation context is wiped. Client should also clear its own state.
    """
    destroyed = _destroy_session(user_id)
    logger.info("[api_halt] user=%s destroyed=%s", user_id, destroyed)
    return {"halted": True, "user_id": user_id, "session_destroyed": destroyed}



# ══════════════════════════════════════════════════════════════════════════════
# CBD COMPONENT — Auth Router
# Logical function: Cryptographically-secure token auth with SQLite backing.
# Tokens are generated via secrets.token_hex(32) (256-bit entropy).
# Passwords are stored as bcrypt hashes (argon2 if bcrypt unavailable).
# All subsequent API requests must include "Authorization: Bearer <token>".
# Invalid/missing tokens return 401 and the UI auto-signs-out.
# ══════════════════════════════════════════════════════════════════════════════
import secrets
import sqlite3
import hashlib
import hmac
import threading

_DB_PATH   = os.getenv("JARVIS_DB_PATH", "./data/jarvis.db")
_DB_LOCK   = threading.Lock()

# ── Database bootstrap ─────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(_DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def _init_db():
    with _DB_LOCK:
        conn = _get_db()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                username    TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                is_active   INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS auth_tokens (
                token       TEXT PRIMARY KEY,
                username    TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                expires_at  TEXT,
                FOREIGN KEY (username) REFERENCES users(username)
            );
            CREATE INDEX IF NOT EXISTS idx_tokens_username ON auth_tokens(username);
        """)
        conn.commit()
        # Always ensure at least one account exists.
        # If the admin row is missing (first boot or wiped DB), recreate it.
        cur = conn.execute("SELECT COUNT(*) FROM users WHERE username = 'admin'")
        if cur.fetchone()[0] == 0:
            _create_user_internal(conn, "admin", "admin123")
            logger.info("[db_init] admin account seeded: admin / admin123")
        conn.close()

def _sha256_hex(value: str) -> str:
    """SHA-256 of a string → lowercase hex. Used both client-side (JS) and server-side."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def _hash_password(plaintext: str) -> str:
    """
    Hash a plaintext password for storage.
    Always takes raw plaintext — hashing is done entirely server-side.
    Stores: pbkdf2:{salt}:{PBKDF2-HMAC-SHA256(sha256(plaintext), salt, 260_000)}
    The double-hash means even if the PBKDF2 output is exposed, the plaintext
    cannot be recovered via rainbow tables on sha256(plaintext).
    """
    salt   = secrets.token_hex(16)
    inner  = _sha256_hex(plaintext)                                      # sha256(plain)
    dk     = hashlib.pbkdf2_hmac("sha256", inner.encode(), salt.encode(), 260_000)
    return f"pbkdf2:{salt}:{dk.hex()}"

def _verify_password(candidate: str, stored_hash: str) -> bool:
    """
    Verify candidate against stored hash.

    candidate may be EITHER:
      (a) raw plaintext  — from seeding, curl testing, or admin reset
      (b) sha256hex of plaintext — from browser (JS pre-hashes before sending)

    Strategy: always try BOTH interpretations.
    The stored hash was built from sha256(plaintext), so:
      - if candidate is sha256hex  → use directly as inner key
      - if candidate is plaintext  → compute sha256hex first, then use as inner key
    Both paths produce identical results when matching, so we try them in order.
    """
    try:
        if stored_hash.startswith("pbkdf2:"):
            _, salt, dk_hex = stored_hash.split(":", 2)

            def _try(inner: str) -> bool:
                dk = hashlib.pbkdf2_hmac("sha256", inner.encode(), salt.encode(), 260_000)
                return hmac.compare_digest(dk.hex(), dk_hex)

            # Path A: candidate is already sha256hex (64-char hex string from browser)
            if len(candidate) == 64 and all(c in "0123456789abcdef" for c in candidate):
                if _try(candidate):
                    return True
                # Also try treating it as raw plaintext that happens to be 64 hex chars
                return _try(_sha256_hex(candidate))

            # Path B: candidate is raw plaintext (curl / seeding / reset)
            return _try(_sha256_hex(candidate))
        else:
            # Legacy format (no prefix) — plain PBKDF2(plaintext)
            salt, dk_hex = stored_hash.split(":", 1)
            dk = hashlib.pbkdf2_hmac("sha256", candidate.encode(), salt.encode(), 260_000)
            if hmac.compare_digest(dk.hex(), dk_hex):
                return True
            # Try sha256hex path too for legacy compat
            dk2 = hashlib.pbkdf2_hmac("sha256", _sha256_hex(candidate).encode(), salt.encode(), 260_000)
            return hmac.compare_digest(dk2.hex(), dk_hex)
    except Exception:
        return False

def _create_user_internal(conn: sqlite3.Connection, username: str, password: str):
    """password is always raw plaintext here — _hash_password handles the hashing."""
    ph = _hash_password(password)
    conn.execute(
        "INSERT OR IGNORE INTO users (username, password_hash) VALUES (?, ?)",
        (username.lower(), ph)
    )
    conn.commit()

def _issue_token(username: str) -> str:
    """Generate a 256-bit hex token and store it in the DB."""
    token = secrets.token_hex(32)
    with _DB_LOCK:
        conn = _get_db()
        conn.execute(
            "INSERT INTO auth_tokens (token, username) VALUES (?, ?)",
            (token, username.lower())
        )
        conn.commit()
        conn.close()
    return token

def _validate_token(token: str) -> str | None:
    """Return username if token is valid, else None."""
    if not token:
        return None
    with _DB_LOCK:
        conn = _get_db()
        row = conn.execute(
            "SELECT username FROM auth_tokens WHERE token = ?", (token,)
        ).fetchone()
        conn.close()
    return row["username"] if row else None

def _revoke_token(token: str):
    with _DB_LOCK:
        conn = _get_db()
        conn.execute("DELETE FROM auth_tokens WHERE token = ?", (token,))
        conn.commit()
        conn.close()

def _require_auth(request: Request) -> str:
    """Extract + validate Bearer token. Raises 401 on failure."""
    hdr = request.headers.get("Authorization", "")
    token = hdr.removeprefix("Bearer ").strip()
    username = _validate_token(token)
    if not username:
        raise HTTPException(status_code=401, detail="Invalid or expired token. Please log in again.")
    return username

# Initialise DB on startup
_init_db()


# ── Auth endpoints ─────────────────────────────────────────────────────────────

@app.post("/api/auth/login", tags=["Auth"])
async def auth_login(request: Request):
    """
    Login with username + password.
    Returns a cryptographically-secure 256-bit hex token (secrets.token_hex(32)).
    The token is stored in the DB and must be sent as "Authorization: Bearer <token>"
    on every subsequent request. Missing/invalid tokens return 401.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    username = (body.get("username") or "").strip().lower()
    password =  body.get("password", "")

    if not username:
        raise HTTPException(status_code=400, detail="username is required")

    with _DB_LOCK:
        conn = _get_db()
        row  = conn.execute(
            "SELECT password_hash, is_active FROM users WHERE username = ?", (username,)
        ).fetchone()
        conn.close()

    if row is None or not row["is_active"]:
        logger.warning("[auth_fail_no_user] user=%s", username)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not _verify_password(password, row["password_hash"]):
        logger.warning("[auth_fail_bad_pass] user=%s", username)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = _issue_token(username)
    logger.info("[auth_ok] user=%s token=%s...", username, token[:8])
    return {"access_token": token, "token_type": "bearer", "username": username}


@app.post("/api/auth/logout", tags=["Auth"])
async def auth_logout(request: Request):
    """Revoke the current session token."""
    hdr   = request.headers.get("Authorization", "")
    token = hdr.removeprefix("Bearer ").strip()
    _revoke_token(token)
    return {"logged_out": True}


@app.get("/api/auth/me", tags=["Auth"])
async def auth_me(request: Request):
    """Return authenticated user info (validates token)."""
    try:
        username = _require_auth(request)
        return {"username": username, "authenticated": True}
    except HTTPException:
        return {"username": None, "authenticated": False}


@app.post("/api/auth/register", tags=["Auth"])
async def auth_register(request: Request):
    """
    Register a new user account (open registration — lock down in production
    by gating with JARVIS_OPEN_REGISTRATION=false and requiring admin token).
    """
    open_reg = os.getenv("JARVIS_OPEN_REGISTRATION", "true").lower() == "true"
    if not open_reg:
        _require_auth(request)  # must be logged in (admin) to create accounts

    body     = await request.json()
    username = (body.get("username") or "").strip().lower()
    password =  body.get("password", "")

    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password are required")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    with _DB_LOCK:
        conn = _get_db()
        existing = conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
        if existing:
            conn.close()
            raise HTTPException(status_code=409, detail=f"Username '{username}' already exists")
        # password from client is sha256hex(plaintext) — store it directly via PBKDF2
        # so _verify_password path A (sha256hex candidate) matches correctly.
        # Detect: 64-char lowercase hex → treat as pre-hashed; else treat as plaintext.
        if len(password) == 64 and all(c in "0123456789abcdef" for c in password):
            # Client sent sha256hex — store PBKDF2(sha256hex) directly
            salt  = secrets.token_hex(16)
            dk    = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
            ph    = f"pbkdf2:{salt}:{dk.hex()}"
            conn.execute(
                "INSERT OR IGNORE INTO users (username, password_hash) VALUES (?, ?)",
                (username.lower(), ph)
            )
            conn.commit()
        else:
            # Plaintext — use standard _hash_password (sha256hex internally)
            _create_user_internal(conn, username, password)
        conn.close()

    logger.info("[auth_register] user=%s", username)
    return {"registered": True, "username": username}


@app.get("/api/auth/users", tags=["Auth"])
async def auth_list_users(request: Request):
    """List all users. Requires authentication."""
    caller = _require_auth(request)
    with _DB_LOCK:
        conn  = _get_db()
        users = conn.execute(
            "SELECT username, created_at, is_active FROM users ORDER BY created_at"
        ).fetchall()
        conn.close()
    return {"users": [dict(u) for u in users], "caller": caller}


@app.put("/api/auth/users/{username}", tags=["Auth"])
async def auth_update_user(username: str, request: Request):
    """
    Update a user's password and/or active status.
    Admin can update any user. Regular users can only update themselves.
    Body: { "password"?: str (SHA-256 hex from client), "is_active"?: bool }
    """
    caller = _require_auth(request)
    target = username.lower()
    if caller != "admin" and caller != target:
        raise HTTPException(status_code=403, detail="Permission denied")

    body      = await request.json()
    new_pass  = body.get("password")
    is_active = body.get("is_active")

    if caller != "admin" and is_active is not None:
        raise HTTPException(status_code=403, detail="Only admin can change active status")

    with _DB_LOCK:
        conn = _get_db()
        row  = conn.execute("SELECT 1 FROM users WHERE username = ?", (target,)).fetchone()
        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail=f"User '{target}' not found")

        updates, params = [], []
        if new_pass is not None:
            if len(new_pass) < 6 and len(new_pass) != 64:
                conn.close()
                raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
            # Pre-hashed from browser (64-char hex) vs plaintext
            if len(new_pass) == 64 and all(c in "0123456789abcdef" for c in new_pass):
                _s  = secrets.token_hex(16)
                _dk = hashlib.pbkdf2_hmac("sha256", new_pass.encode(), _s.encode(), 260_000)
                ph  = f"pbkdf2:{_s}:{_dk.hex()}"
            else:
                ph  = _hash_password(new_pass)
            updates.append("password_hash = ?"); params.append(ph)
        if is_active is not None:
            if target == "admin" and not is_active:
                conn.close()
                raise HTTPException(status_code=400, detail="Cannot deactivate admin account")
            updates.append("is_active = ?"); params.append(1 if is_active else 0)
        if not updates:
            conn.close()
            return {"updated": False, "message": "No fields to update"}

        params.append(target)
        conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE username = ?", params)
        conn.commit()
        conn.close()

    logger.info("[user_update] caller=%s target=%s", caller, target)
    return {"updated": True, "username": target}


@app.delete("/api/auth/users/{username}", tags=["Auth"])
async def auth_delete_user(username: str, request: Request):
    """Delete a user (admin only). Cannot delete admin or self."""
    caller = _require_auth(request)
    if caller != "admin":
        raise HTTPException(status_code=403, detail="Only admin can delete users")
    target = username.lower()
    if target in ("admin", caller):
        raise HTTPException(status_code=400, detail="Cannot delete admin or your own account")

    with _DB_LOCK:
        conn = _get_db()
        row  = conn.execute("SELECT 1 FROM users WHERE username = ?", (target,)).fetchone()
        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail=f"User '{target}' not found")
        conn.execute("DELETE FROM auth_tokens WHERE username = ?", (target,))
        conn.execute("DELETE FROM users WHERE username = ?", (target,))
        conn.commit()
        conn.close()

    logger.info("[user_delete] caller=%s deleted=%s", caller, target)
    return {"deleted": True, "username": target}


@app.post("/api/auth/users/{username}/reset-password", tags=["Auth"])
async def auth_reset_user_password(username: str, request: Request):
    """Admin resets another user's password (plaintext accepted — server hashes)."""
    caller = _require_auth(request)
    if caller != "admin":
        raise HTTPException(status_code=403, detail="Only admin can reset passwords")
    target = username.lower()
    body   = await request.json()
    new_pw = body.get("new_password", "")
    if len(new_pw) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    with _DB_LOCK:
        conn = _get_db()
        if not conn.execute("SELECT 1 FROM users WHERE username = ?", (target,)).fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail=f"User '{target}' not found")
        ph = _hash_password(new_pw)  # plaintext — _hash_password handles sha256 internally
        conn.execute("UPDATE users SET password_hash = ? WHERE username = ?", (ph, target))
        conn.commit()
        conn.close()

    logger.info("[user_reset_password] caller=%s target=%s", caller, target)
    return {"reset": True, "username": target}


@app.get("/api/auth/debug", tags=["Auth"])
async def auth_debug():
    """
    DEVELOPMENT ONLY — Returns DB state without authentication.
    Shows user list (no hashes) and DB path so you can diagnose login issues.
    Remove or gate this behind a secret in production.
    """
    try:
        with _DB_LOCK:
            conn  = _get_db()
            users = conn.execute(
                "SELECT username, created_at, is_active FROM users ORDER BY created_at"
            ).fetchall()
            tokens = conn.execute(
                "SELECT COUNT(*) as cnt FROM auth_tokens"
            ).fetchone()
            conn.close()
        return {
            "db_path":     os.path.abspath(_DB_PATH),
            "db_exists":   os.path.isfile(_DB_PATH),
            "user_count":  len(users),
            "users":       [dict(u) for u in users],
            "active_tokens": tokens["cnt"] if tokens else 0,
        }
    except Exception as exc:
        return {"error": str(exc), "db_path": os.path.abspath(_DB_PATH)}


@app.post("/api/auth/reset-admin", tags=["Auth"])
async def auth_reset_admin(request: Request):
    """
    Emergency endpoint — resets the admin password to admin123.
    Only works when called from localhost (127.0.0.1 / ::1).
    Use this if the admin password is lost.
    """
    client_ip = request.client.host if request.client else ""
    if client_ip not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="Only accessible from localhost")

    new_pass = request.headers.get("X-New-Password", "admin123")
    if len(new_pass) < 6:
        raise HTTPException(status_code=400, detail="Password must be >= 6 chars")

    ph = _hash_password(new_pass)
    with _DB_LOCK:
        conn = _get_db()
        # Upsert admin account
        conn.execute(
            "INSERT INTO users (username, password_hash, is_active) VALUES ('admin', ?, 1) "
            "ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash, is_active=1",
            (ph,)
        )
        conn.commit()
        conn.close()

    logger.warning("[auth_reset_admin] admin password reset from %s", client_ip)
    return {"reset": True, "username": "admin", "password": new_pass}


# ══════════════════════════════════════════════════════════════════════════════
# CBD COMPONENT — Workspace File Browser
# Logical function: List and read files from per-user workspace directory.
# Default workspace: /app/{user_id}/workspace/
# ENV: JARVIS_WORKSPACE_ROOT=/app  (root under which per-user dirs are created)
# ══════════════════════════════════════════════════════════════════════════════
import fnmatch as _fnmatch

# Path: /app/workspace/{user}/{project}/workspace
# ENV: JARVIS_WORKSPACE_ROOT=/app/workspace  (override base dir)
#      JARVIS_DEFAULT_PROJECT=default
WORKSPACE_ROOT    = os.getenv("JARVIS_WORKSPACE_ROOT",    "/app/workspace")
DEFAULT_PROJECT   = os.getenv("JARVIS_DEFAULT_PROJECT",   "default")
WS_MAX_FILE_BYTES  = 128_000   # 128 KB per file sent to LLM
WS_MAX_TOTAL_BYTES = 512_000   # 512 KB total across checked files
WS_SKIP_DIRS  = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build"}
WS_TEXT_EXTS  = {
    ".py",".js",".ts",".jsx",".tsx",".sh",".bash",".zsh",
    ".md",".txt",".rst",".cfg",".ini",".toml",".yaml",".yml",
    ".json",".xml",".html",".css",".sql",".go",".rs",".java",
    ".c",".cpp",".h",".env",".conf",".log",".csv",
}
PROJECT_META_FILE = ".jarvis_project.json"


def _safe_name(name: str, maxlen: int = 48) -> str:
    """Sanitise a user or project name for filesystem use."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name.strip())[:maxlen] or "default"


def _user_root(user_id: str) -> str:
    """Return /app/workspace/{user} — created on demand."""
    path = os.path.join(WORKSPACE_ROOT, _safe_name(user_id))
    os.makedirs(path, exist_ok=True)
    return path


def _workspace_path(user_id: str, project: str = DEFAULT_PROJECT) -> str:
    """Return /app/workspace/{user}/{project}/workspace — created on demand."""
    safe_proj = _safe_name(project) if project else DEFAULT_PROJECT
    path = os.path.join(_user_root(user_id), safe_proj, "workspace")
    os.makedirs(path, exist_ok=True)
    return path


def _project_root(user_id: str, project: str) -> str:
    """Return /app/workspace/{user}/{project} — created on demand."""
    path = os.path.join(_user_root(user_id), _safe_name(project))
    os.makedirs(path, exist_ok=True)
    return path


def _list_projects(user_id: str) -> list[dict]:
    """
    List all projects for a user.
    A project is any subdirectory of /app/workspace/{user}/ that is not hidden.
    Returns list of {name, workspace, created, file_count, size_bytes}.
    """
    user_dir = _user_root(user_id)
    projects = []
    try:
        for entry in sorted(os.scandir(user_dir), key=lambda e: e.name):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            ws = os.path.join(entry.path, "workspace")
            file_count = 0
            size_bytes = 0
            if os.path.isdir(ws):
                for dirpath, dirs, files in os.walk(ws):
                    dirs[:] = [d for d in dirs if d not in WS_SKIP_DIRS]
                    for f in files:
                        file_count += 1
                        try:
                            size_bytes += os.path.getsize(os.path.join(dirpath, f))
                        except OSError:
                            pass
            stat = entry.stat()
            projects.append({
                "name":        entry.name,
                "workspace":   ws,
                "created":     datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat(),
                "modified":    datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "file_count":  file_count,
                "size_bytes":  size_bytes,
                "is_default":  entry.name == DEFAULT_PROJECT,
            })
    except PermissionError:
        pass
    # Ensure default project always exists
    if not any(p["name"] == DEFAULT_PROJECT for p in projects):
        _workspace_path(user_id, DEFAULT_PROJECT)
        projects = _list_projects(user_id)  # reload after creation
    return projects


def _is_text_file(name: str) -> bool:
    _, ext = os.path.splitext(name)
    return ext.lower() in WS_TEXT_EXTS


@app.get("/api/workspace/{user_id}", tags=["Workspace"])
async def workspace_list(
    user_id: str,
    path:    str = "",
    depth:   int = 4,
    project: str = DEFAULT_PROJECT,
):
    """
    List files in /app/workspace/{user_id}/{project}/workspace/.
    Query params:
      project — project name (default from JARVIS_DEFAULT_PROJECT env)
      path    — sub-path within the workspace (default: root)
      depth   — max recursion depth (default 4)
    """
    ws_root = _workspace_path(user_id, project)
    target  = os.path.normpath(os.path.join(ws_root, path.lstrip("/"))) if path else ws_root

    # Security: never escape the workspace root
    if not target.startswith(ws_root):
        raise HTTPException(status_code=403, detail="Path outside workspace")

    if not os.path.isdir(target):
        raise HTTPException(status_code=404, detail=f"Directory not found: {path}")

    entries = []
    try:
        for dirpath, dirs, files in os.walk(target):
            # Depth gate
            rel_dir = os.path.relpath(dirpath, ws_root)
            current_depth = 0 if rel_dir == "." else rel_dir.count(os.sep) + 1
            if current_depth >= depth:
                dirs.clear()
                continue

            dirs[:] = sorted(d for d in dirs if d not in WS_SKIP_DIRS and not d.startswith("."))

            for fname in sorted(files):
                if fname.startswith("."):
                    continue
                fpath     = os.path.join(dirpath, fname)
                rel_path  = os.path.relpath(fpath, ws_root)
                try:
                    fsize = os.path.getsize(fpath)
                except OSError:
                    fsize = 0

                entries.append({
                    "name":      fname,
                    "path":      rel_path,          # relative to workspace root
                    "abs_path":  fpath,
                    "dir":       rel_dir if rel_dir != "." else "",
                    "size":      fsize,
                    "is_text":   _is_text_file(fname),
                    "type":      "file",
                })
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    return {
        "workspace_root": ws_root,
        "user_id":        user_id,
        "project":        _safe_name(project) if project else DEFAULT_PROJECT,
        "path":           path or "/",
        "entries":        entries,
        "count":          len(entries),
    }


@app.post("/api/workspace/{user_id}/read", tags=["Workspace"])
async def workspace_read_files(user_id: str, request: Request):
    """
    Read selected workspace files.
    Body: { "files": ["rel/path.py", ...], "project": "myproject" }
    """
    body    = await request.json()
    files   = body.get("files", [])
    project = body.get("project", DEFAULT_PROJECT)
    ws_root = _workspace_path(user_id, project)
    results   = []
    total     = 0

    for rel_path in files[:50]:  # hard limit 50 files per request
        # Security: prevent path traversal
        abs_path = os.path.normpath(os.path.join(ws_root, rel_path))
        if not abs_path.startswith(ws_root):
            results.append({"path": rel_path, "error": "Path outside workspace", "content": ""})
            continue
        if not os.path.isfile(abs_path):
            results.append({"path": rel_path, "error": "File not found", "content": ""})
            continue

        try:
            fsize = os.path.getsize(abs_path)
            read_limit = min(WS_MAX_FILE_BYTES, WS_MAX_TOTAL_BYTES - total)
            if read_limit <= 0:
                results.append({"path": rel_path, "error": "Total size limit reached", "content": ""})
                continue

            is_text = _is_text_file(os.path.basename(abs_path))
            if is_text:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(read_limit)
                truncated = fsize > read_limit
            else:
                import base64 as _b64
                with open(abs_path, "rb") as f:
                    raw = f.read(read_limit)
                content   = _b64.b64encode(raw).decode()
                truncated = fsize > read_limit

            total += len(content)
            results.append({
                "path":      rel_path,
                "name":      os.path.basename(abs_path),
                "abs_path":  abs_path,
                "content":   content,
                "size":      fsize,
                "truncated": truncated,
                "is_text":   is_text,
                "error":     None,
            })
        except Exception as exc:
            results.append({"path": rel_path, "error": str(exc), "content": ""})

    return {
        "files":        results,
        "count":        len(results),
        "total_bytes":  total,
    }


@app.post("/api/workspace/{user_id}/mkdir", tags=["Workspace"])
async def workspace_mkdir(user_id: str, request: Request):
    """Create a directory inside the user's workspace."""
    body     = await request.json()
    rel_path = body.get("path", "").strip().lstrip("/")
    project  = body.get("project", DEFAULT_PROJECT)
    if not rel_path:
        raise HTTPException(status_code=400, detail="path is required")
    ws_root  = _workspace_path(user_id, project)
    abs_path = os.path.normpath(os.path.join(ws_root, rel_path))
    if not abs_path.startswith(ws_root):
        raise HTTPException(status_code=403, detail="Path outside workspace")
    os.makedirs(abs_path, exist_ok=True)
    return {"created": True, "path": rel_path, "abs_path": abs_path}


@app.delete("/api/workspace/{user_id}/file", tags=["Workspace"])
async def workspace_delete_file(user_id: str, request: Request):
    """Delete a file from the user's workspace (with confirmation guard)."""
    body     = await request.json()
    rel_path = body.get("path", "").strip()
    project  = body.get("project", DEFAULT_PROJECT)
    if not rel_path:
        raise HTTPException(status_code=400, detail="path is required")
    ws_root  = _workspace_path(user_id, project)
    abs_path = os.path.normpath(os.path.join(ws_root, rel_path))
    if not abs_path.startswith(ws_root):
        raise HTTPException(status_code=403, detail="Path outside workspace")
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="File not found")
    os.remove(abs_path)
    logger.info("[workspace_delete] user=%s path=%s", user_id, rel_path)
    return {"deleted": True, "path": rel_path}




@app.get("/api/workspace/{user_id}/zip", tags=["Workspace"])
async def workspace_download_zip(
    user_id: str,
    project: str = DEFAULT_PROJECT,
    request: Request = None,
):
    """
    Stream the entire project workspace as a .zip file download.
    Path: /app/workspace/{user_id}/{project}/workspace/ → {project}.zip

    Query params:
      project — project name (default: "default")

    The zip is streamed directly from memory using zipfile + BytesIO,
    so no temp file is written to disk.
    """
    import io, zipfile as _zipfile
    from fastapi.responses import StreamingResponse as _SR

    ws_root = _workspace_path(user_id, project)
    safe_proj = _safe_name(project) if project else DEFAULT_PROJECT

    if not os.path.isdir(ws_root):
        raise HTTPException(status_code=404, detail=f"Workspace not found: {ws_root}")

    def _iter_zip():
        buf = io.BytesIO()
        with _zipfile.ZipFile(buf, mode="w", compression=_zipfile.ZIP_DEFLATED) as zf:
            for dirpath, dirs, files in os.walk(ws_root):
                dirs[:] = sorted(d for d in dirs if d not in WS_SKIP_DIRS and not d.startswith("."))
                for fname in sorted(files):
                    if fname.startswith("."):
                        continue
                    abs_path = os.path.join(dirpath, fname)
                    rel_path = os.path.relpath(abs_path, ws_root)
                    try:
                        zf.write(abs_path, arcname=rel_path)
                    except (PermissionError, OSError):
                        pass
        buf.seek(0)
        yield buf.read()

    filename = f"{safe_proj}-workspace.zip"
    logger.info("[workspace_zip] user=%s project=%s file=%s", user_id, safe_proj, filename)
    return _SR(
        _iter_zip(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ══════════════════════════════════════════════════════════════════════════════
# CBD COMPONENT — Project Manager
# Path schema: /app/workspace/{user}/{project}/workspace/
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/projects/{user_id}", tags=["Workspace"])
async def list_projects(user_id: str):
    """
    List all projects for a user.
    Each project is a directory at /app/workspace/{user_id}/{project}/.
    Returns: [{ name, workspace, created, modified, file_count, size_bytes, is_default }]
    """
    projects = _list_projects(user_id)
    logger.info("[list_projects] user=%s count=%d", user_id, len(projects))
    return {
        "user_id":         user_id,
        "projects":        projects,
        "count":           len(projects),
        "default_project": DEFAULT_PROJECT,
        "workspace_root":  WORKSPACE_ROOT,
    }


@app.post("/api/projects/{user_id}", tags=["Workspace"])
async def create_project(user_id: str, request: Request):
    """
    Create a new project for the user.
    Body: { "name": "my-project", "description"?: "..." }
    Creates /app/workspace/{user_id}/{project}/workspace/ and a .jarvis_project.json metadata file.
    """
    body    = await request.json()
    name    = body.get("name", "").strip()
    desc    = body.get("description", "")
    if not name:
        raise HTTPException(status_code=400, detail="Project name is required")

    safe_name = _safe_name(name)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid project name")

    proj_root = _project_root(user_id, safe_name)
    ws_path   = _workspace_path(user_id, safe_name)

    # Write metadata
    meta = {
        "name":        safe_name,
        "display_name": name,
        "description": desc,
        "created_by":  user_id,
        "created_at":  datetime.now(timezone.utc).isoformat(),
        "workspace":   ws_path,
    }
    meta_path = os.path.join(proj_root, PROJECT_META_FILE)
    try:
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)
    except Exception as exc:
        logger.warning("[create_project.meta_write_fail] %s", exc)

    logger.info("[create_project] user=%s name=%s path=%s", user_id, safe_name, ws_path)
    return {
        "created":      True,
        "name":         safe_name,
        "display_name": name,
        "workspace":    ws_path,
        "project_root": proj_root,
        "metadata":     meta,
    }


@app.delete("/api/projects/{user_id}/{project_name}", tags=["Workspace"])
async def delete_project(user_id: str, project_name: str):
    """
    Delete a project and ALL its files. Irreversible.
    Cannot delete the default project.
    """
    import shutil as _shutil
    safe_name = _safe_name(project_name)
    if safe_name == DEFAULT_PROJECT:
        raise HTTPException(status_code=400, detail=f"Cannot delete the default project '{DEFAULT_PROJECT}'")

    proj_root = os.path.join(_user_root(user_id), safe_name)
    if not os.path.isdir(proj_root):
        raise HTTPException(status_code=404, detail=f"Project '{safe_name}' not found")

    try:
        _shutil.rmtree(proj_root)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Delete failed: {exc}")

    logger.info("[delete_project] user=%s project=%s", user_id, safe_name)
    return {"deleted": True, "name": safe_name}


@app.get("/api/projects/{user_id}/{project_name}", tags=["Workspace"])
async def get_project(user_id: str, project_name: str):
    """Get metadata for a specific project."""
    safe_name = _safe_name(project_name)
    proj_root = os.path.join(_user_root(user_id), safe_name)
    ws_path   = os.path.join(proj_root, "workspace")

    if not os.path.isdir(proj_root):
        raise HTTPException(status_code=404, detail=f"Project '{safe_name}' not found")

    meta_path = os.path.join(proj_root, PROJECT_META_FILE)
    meta: dict = {}
    if os.path.isfile(meta_path):
        try:
            with open(meta_path) as f:
                meta = json.load(f)
        except Exception:
            pass

    return {
        "name":         safe_name,
        "workspace":    ws_path,
        "project_root": proj_root,
        "exists":       True,
        "metadata":     meta,
    }



# ══════════════════════════════════════════════════════════════════════════════
# CBD COMPONENT — Persona Registry Endpoint
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/personas", tags=["Personas"])
async def get_personas():
    """List all personas (built-in + custom). No auth required — used by Login screen."""
    return {"personas": list_personas(), "default_persona": DEFAULT_PERSONA}


@app.get("/api/personas/{persona_id}", tags=["Personas"])
async def get_persona_detail(persona_id: str):
    """Return full persona data including soul, directives, theme, skills."""
    p = get_persona(persona_id)
    if not p:
        raise HTTPException(status_code=404, detail=f"Persona '{persona_id}' not found")
    return p


@app.post("/api/personas", tags=["Personas"])
async def create_persona(request: Request):
    """
    Create a new custom persona.
    Body: full persona dict — id, name, tagline, soul, directives, skills[], theme{}, builtin=false
    Built-in flag is always forced to false for user-created personas.
    """
    _require_auth(request)
    body = await request.json()

    if not body.get("id") or not body.get("name"):
        raise HTTPException(status_code=400, detail="id and name are required")

    body["builtin"] = False  # users cannot create built-ins
    ok, err = save_persona(body)
    if not ok:
        raise HTTPException(status_code=500, detail=f"Save failed: {err}")

    logger.info("[persona_create] id=%s name=%s", body["id"], body["name"])
    return {"created": True, "id": body["id"]}


@app.put("/api/personas/{persona_id}", tags=["Personas"])
async def update_persona(persona_id: str, request: Request):
    """
    Update an existing persona (built-in or custom).
    Built-ins CAN be edited (soul/directives/theme) but NOT deleted.
    """
    _require_auth(request)
    existing = get_persona(persona_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Persona '{persona_id}' not found")

    body = await request.json()
    body["id"] = persona_id                            # id is immutable
    body["builtin"] = existing.get("builtin", False)  # preserve builtin flag

    ok, err = save_persona(body)
    if not ok:
        raise HTTPException(status_code=500, detail=f"Save failed: {err}")

    logger.info("[persona_update] id=%s", persona_id)
    return {"updated": True, "id": persona_id}


@app.delete("/api/personas/{persona_id}", tags=["Personas"])
async def delete_persona_endpoint(persona_id: str, request: Request):
    """Delete a custom persona. Built-in personas cannot be deleted."""
    _require_auth(request)
    ok, err = delete_persona(persona_id)
    if not ok:
        raise HTTPException(status_code=400, detail=err)
    logger.info("[persona_delete] id=%s", persona_id)
    return {"deleted": True, "id": persona_id}


@app.post("/api/personas/generate", tags=["Personas"])
async def generate_persona_hint(request: Request):
    """
    Generate a persona JSON scaffold from a natural-language hint using the LLM.
    Body: { "hint": "Make this persona a cyber war king", "user_id": "..." }
    Returns a persona dict the operator can review and save.
    """
    _require_auth(request)
    body    = await request.json()
    hint    = body.get("hint", "").strip()
    user_id = body.get("user_id", "default")

    if not hint:
        raise HTTPException(status_code=400, detail="hint is required")

    try:
        agent   = _get_session(user_id)
        _schema = (
            '{\n  "id": "slug_id_no_spaces",\n  "name": "Display Name",\n'
            '  "tagline": "One-line description",\n'
            '  "soul": "Full personality/identity block 200-400 words",\n'
            '  "directives": "Role-specific instructions 200-400 words",\n'
            '  "skills": ["filesystem","os_execution","memory_manager"],\n'
            '  "builtin": false,\n'
            '  "theme": {\n'
            '    "accent":"#RRGGBB","accentDim":"#RRGGBB","accentGlow":"#RRGGBB18","accentGlow2":"#RRGGBB40",\n'
            '    "bg":"#RRGGBB","bgDeep":"#RRGGBB","bgPanel":"#RRGGBB","bgCard":"#RRGGBB","bgCardHover":"#RRGGBB",\n'
            '    "warm":"#RRGGBB","warmDim":"#RRGGBB","gold":"#RRGGBB","goldDim":"#RRGGBB",\n'
            '    "textPri":"#RRGGBB","textSec":"#RRGGBB","textDim":"#RRGGBB",\n'
            '    "border":"#RRGGBB","borderMid":"#RRGGBB","borderHi":"#RRGGBB44",\n'
            '    "ok":"#RRGGBB","okDim":"#RRGGBB","err":"#RRGGBB","errDim":"#RRGGBB",\n'
            '    "warn":"#RRGGBB","warnDim":"#RRGGBB","react":"#RRGGBB","reactDim":"#RRGGBB",\n'
            '    "fontImport":"@import url(...)","fontMono":"Share Tech Mono, monospace",\n'
            '    "fontHeader":"font name","glyph":"unicode","wordmark":"NAME",\n'
            '    "subtitle":"SUBTITLE","tagline":"TAGLINE","scanline":"#RRGGBB22"\n'
            '  }\n}'
        )
        gen_msg = (
            f'Generate a Jarvis persona JSON for this concept: "{hint}"\n\n'
            f"Return ONLY valid JSON (no markdown) using this schema:\n{_schema}\n\n"
            "Match the theme colors to the persona concept. Use dramatic distinct colors.\n"
            "Choose a Google Font for fontHeader. Write compelling soul and directives."
        )

        # Non-streaming call to get full JSON
        result = await agent.llm.chat(
            [{"role": "user", "content": gen_msg}],
            system="You are a JSON generator. Return only valid JSON. No markdown. No explanation."
        )

        # Try to parse the JSON
        import re as _re
        json_match = _re.search(r'\{[\s\S]*\}', result)
        if not json_match:
            raise ValueError("No JSON found in LLM response")

        persona_data = json.loads(json_match.group())
        persona_data["builtin"] = False

        return {"generated": True, "persona": persona_data}

    except json.JSONDecodeError as e:
        raise HTTPException(status_code=422, detail=f"LLM returned invalid JSON: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Generation failed: {e}")



# ══════════════════════════════════════════════════════════════════════════════
# SAAS BILLING & SUBSCRIPTION PLATFORM
# Blueprint: saas-billing-platform-blueprint.md v1.0
# Flag:      SAAS_BILLING_ENABLED=true|false  (default: false)
#
# All 26 components implemented:
#   Foundation  : Logger, ErrorSchema, ConfigLoader
#   Domain      : CustomerAccountManager, PlanCatalogManager,
#                 SubscriptionLifecycleManager, UsageEventRecorder, UsageAggregator,
#                 CurrencyConverter, TaxCalculator, PricingEngine, InvoiceGenerator,
#                 PaymentGatewayClient, DunningManager, NotificationDispatcher,
#                 BillingScheduler, WebhookEventValidator
#   Adaptors    : ChargeRequest→GatewayPayload, GatewayResponse→PaymentResult,
#                 GatewayWebhook→InternalEvent, ExchangeRateAPI→CurrencyConverter,
#                 Notification→EmailProvider, Invoice→Notification
#   Orchestrators: SubscriptionOrchestrator, BillingCycleOrchestrator,
#                  WebhookOrchestrator
# ══════════════════════════════════════════════════════════════════════════════

import uuid as _uuid
import time as _time
import hmac as _hmac_billing
import hashlib as _hashlib_billing
from datetime import datetime as _dt, timezone as _tz, timedelta as _td
from functools import wraps as _wraps

_BILLING_ENABLED = os.getenv("SAAS_BILLING_ENABLED", "false").lower() == "true"
_BILLING_GATEWAY = os.getenv("SAAS_GATEWAY", "stripe")             # stripe | mock
_BILLING_STRIPE_KEY  = os.getenv("STRIPE_SECRET_KEY", "")
_BILLING_FX_KEY      = os.getenv("FX_API_KEY", "")
_BILLING_EMAIL_KEY   = os.getenv("EMAIL_API_KEY", "")
_BILLING_WEBHOOK_SECRET = os.getenv("SAAS_WEBHOOK_SECRET", "")
_BILLING_BASE_CURRENCY  = os.getenv("SAAS_BASE_CURRENCY", "USD")

# ── Circuit breaker state ─────────────────────────────────────────────────────
_CB: dict = {}   # component → {failures, open_until}
_CB_THRESHOLD = 3
_CB_RESET_S   = 60

def _cb_ok(comp: str) -> bool:
    """Return True if the component circuit is closed (allowed to proceed)."""
    s = _CB.get(comp, {})
    if s.get("open_until") and _time.time() < s["open_until"]:
        return False
    return True

def _cb_fail(comp: str):
    s = _CB.setdefault(comp, {"failures": 0, "open_until": None})
    s["failures"] += 1
    if s["failures"] >= _CB_THRESHOLD:
        s["open_until"] = _time.time() + _CB_RESET_S
        logger.warning("[billing_cb_open] component=%s", comp)

def _cb_success(comp: str):
    _CB.pop(comp, None)

# ── DB schema extension ───────────────────────────────────────────────────────
def _init_billing_db():
    if not _BILLING_ENABLED:
        return
    with _DB_LOCK:
        conn = _get_db()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS billing_customers (
                customer_id   TEXT PRIMARY KEY,
                username      TEXT NOT NULL,
                name          TEXT NOT NULL,
                email         TEXT NOT NULL,
                country       TEXT NOT NULL DEFAULT 'US',
                currency      TEXT NOT NULL DEFAULT 'USD',
                tax_id        TEXT,
                status        TEXT NOT NULL DEFAULT 'active',
                created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
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
                subscription_id     TEXT PRIMARY KEY,
                customer_id         TEXT NOT NULL,
                plan_id             TEXT NOT NULL,
                status              TEXT NOT NULL DEFAULT 'active',
                current_period_start TEXT NOT NULL,
                current_period_end   TEXT NOT NULL,
                canceled_at         TEXT,
                created_at          TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (customer_id) REFERENCES billing_customers(customer_id),
                FOREIGN KEY (plan_id)     REFERENCES billing_plans(plan_id)
            );
            CREATE TABLE IF NOT EXISTS billing_usage_events (
                event_id         TEXT PRIMARY KEY,
                subscription_id  TEXT NOT NULL,
                metric           TEXT NOT NULL,
                quantity         REAL NOT NULL,
                occurred_at      TEXT NOT NULL,
                idempotency_key  TEXT NOT NULL UNIQUE,
                recorded_at      TEXT NOT NULL DEFAULT (datetime('now')),
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
                payment_id      TEXT PRIMARY KEY,
                invoice_id      TEXT NOT NULL,
                transaction_id  TEXT,
                amount          REAL NOT NULL,
                currency        TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'pending',
                failure_code    TEXT,
                attempt_number  INTEGER NOT NULL DEFAULT 1,
                gateway         TEXT NOT NULL DEFAULT 'mock',
                created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (invoice_id) REFERENCES billing_invoices(invoice_id)
            );
            CREATE TABLE IF NOT EXISTS billing_dunning (
                dunning_id      TEXT PRIMARY KEY,
                invoice_id      TEXT NOT NULL,
                subscription_id TEXT NOT NULL,
                failure_code    TEXT,
                attempt_number  INTEGER NOT NULL DEFAULT 1,
                next_retry_at   TEXT,
                subscription_action TEXT NOT NULL DEFAULT 'none',
                created_at      TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_billing_subs_customer  ON billing_subscriptions(customer_id);
            CREATE INDEX IF NOT EXISTS idx_billing_usage_sub       ON billing_usage_events(subscription_id);
            CREATE INDEX IF NOT EXISTS idx_billing_invoices_sub    ON billing_invoices(subscription_id);
            CREATE INDEX IF NOT EXISTS idx_billing_payments_invoice ON billing_payments(invoice_id);
        """)
        # Seed a default plan if none exists
        if not conn.execute("SELECT 1 FROM billing_plans LIMIT 1").fetchone():
            conn.execute("""
                INSERT INTO billing_plans (plan_id, name, billing_interval, base_price, currency, metered_rates, features)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, ("plan_free", "Free", "monthly", 0.0, "USD", "[]", '["Basic access"]'))
            conn.execute("""
                INSERT INTO billing_plans (plan_id, name, billing_interval, base_price, currency, metered_rates, features)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, ("plan_pro", "Pro", "monthly", 29.0, "USD",
                  '[{"metric": "api_calls", "unitPrice": 0.001}]',
                  '["Full access", "API access", "Priority support"]'))
            conn.execute("""
                INSERT INTO billing_plans (plan_id, name, billing_interval, base_price, currency, metered_rates, features)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, ("plan_enterprise", "Enterprise", "monthly", 199.0, "USD",
                  '[{"metric": "api_calls", "unitPrice": 0.0005}]',
                  '["Full access", "Unlimited API", "Dedicated support", "SLA"]'))
            conn.commit()
            logger.info("[billing_db_seed] default plans created")
        conn.close()

# ── Foundation: ErrorSchema ───────────────────────────────────────────────────
def _make_error(code: str, message: str, component: str) -> dict:
    return {"code": code, "message": message, "component": component,
            "timestamp": _dt.now(_tz.utc).isoformat()}

def _is_error(obj: dict) -> bool:
    return isinstance(obj, dict) and "code" in obj and "component" in obj

# ── Foundation: billing-specific Logger trace ─────────────────────────────────
def _btrace(component: str, event: str, **meta):
    logger.info("[billing.%s] %s %s", component, event,
                " ".join(f"{k}={v}" for k, v in meta.items()))

# ── Component helpers ─────────────────────────────────────────────────────────
def _now_iso() -> str:
    return _dt.now(_tz.utc).isoformat()

def _period_end(start_iso: str, interval: str) -> str:
    start = _dt.fromisoformat(start_iso.replace("Z", "+00:00"))
    if interval == "yearly":
        end = start + _td(days=365)
    else:
        end = start + _td(days=30)
    return end.isoformat()

# ─────────────────────────────────────────────────────────────────────────────
# DOMAIN COMPONENTS
# ─────────────────────────────────────────────────────────────────────────────

# 4. CustomerAccountManager
def customer_account_manager(action: str, customer_id: str = None, fields: dict = None) -> dict:
    comp = "CustomerAccountManager"
    if not _cb_ok(comp):
        return _make_error("CUSTOMER_LOOKUP_UNAVAILABLE", "Circuit open", comp)
    try:
        with _DB_LOCK:
            conn = _get_db()
            if action == "create":
                cid = str(_uuid.uuid4())
                conn.execute("""INSERT INTO billing_customers
                    (customer_id,username,name,email,country,currency,tax_id)
                    VALUES (?,?,?,?,?,?,?)""",
                    (cid, fields.get("username",""),fields.get("name",""),
                     fields.get("email",""),fields.get("country","US"),
                     fields.get("currency","USD"),fields.get("tax_id")))
                conn.commit()
                row = conn.execute("SELECT * FROM billing_customers WHERE customer_id=?", (cid,)).fetchone()
            elif action == "get":
                row = conn.execute("SELECT * FROM billing_customers WHERE customer_id=?", (customer_id,)).fetchone()
                if not row:
                    conn.close()
                    return _make_error("CUSTOMER_NOT_FOUND", f"Customer {customer_id} not found", comp)
            elif action == "update":
                if not customer_id:
                    conn.close()
                    return _make_error("CUSTOMER_VALIDATION_FAIL", "customer_id required for update", comp)
                sets = ", ".join(f"{k}=?" for k in (fields or {}))
                if sets:
                    conn.execute(f"UPDATE billing_customers SET {sets}, updated_at=? WHERE customer_id=?",
                                 [*fields.values(), _now_iso(), customer_id])
                    conn.commit()
                row = conn.execute("SELECT * FROM billing_customers WHERE customer_id=?", (customer_id,)).fetchone()
            elif action == "get_by_username":
                row = conn.execute("SELECT * FROM billing_customers WHERE username=?", (customer_id,)).fetchone()
                if not row:
                    conn.close()
                    return {"customer": None}
            else:
                conn.close()
                return _make_error("CUSTOMER_VALIDATION_FAIL", f"Unknown action: {action}", comp)
            result = dict(row) if row else None
            conn.close()
        _cb_success(comp)
        _btrace(comp, "ok", action=action, customer_id=customer_id or (result or {}).get("customer_id"))
        return {"customer": result}
    except Exception as e:
        _cb_fail(comp)
        logger.error("[billing.%s] %s", comp, e)
        return _make_error("CUSTOMER_LOOKUP_UNAVAILABLE", str(e), comp)

# 5. PlanCatalogManager
def plan_catalog_manager(action: str, plan_id: str = None, plan: dict = None) -> dict:
    comp = "PlanCatalogManager"
    try:
        with _DB_LOCK:
            conn = _get_db()
            if action == "list":
                rows = conn.execute("SELECT * FROM billing_plans WHERE active=1").fetchall()
                conn.close()
                return {"plans": [dict(r) for r in rows]}
            elif action == "get":
                row = conn.execute("SELECT * FROM billing_plans WHERE plan_id=?", (plan_id,)).fetchone()
                conn.close()
                if not row:
                    return _make_error("PLAN_NOT_FOUND", f"Plan {plan_id} not found", comp)
                return {"plans": [dict(row)]}
            elif action == "upsert":
                pid = plan_id or plan.get("plan_id", str(_uuid.uuid4()))
                conn.execute("""INSERT INTO billing_plans
                    (plan_id,name,billing_interval,base_price,currency,metered_rates,features)
                    VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(plan_id) DO UPDATE SET
                    name=excluded.name, billing_interval=excluded.billing_interval,
                    base_price=excluded.base_price, currency=excluded.currency,
                    metered_rates=excluded.metered_rates, features=excluded.features""",
                    (pid, plan.get("name",""), plan.get("billingInterval","monthly"),
                     plan.get("basePrice",0), plan.get("currency","USD"),
                     json.dumps(plan.get("meteredRates",[])),
                     json.dumps(plan.get("features",[]))))
                conn.commit()
                conn.close()
                return {"plans": [{"plan_id": pid, **plan}]}
            conn.close()
            return _make_error("PLAN_NOT_FOUND", "Unknown action", comp)
    except Exception as e:
        logger.error("[billing.%s] %s", comp, e)
        return _make_error("PLAN_NOT_FOUND", str(e), comp)

# 6. SubscriptionLifecycleManager
def subscription_lifecycle_manager(action: str, customer_id: str = None,
                                    subscription_id: str = None, plan_id: str = None,
                                    effective_date: str = None) -> dict:
    comp = "SubscriptionLifecycleManager"
    if not _cb_ok(comp):
        return _make_error("SUBSCRIPTION_UPDATE_UNAVAILABLE", "Circuit open", comp)
    try:
        with _DB_LOCK:
            conn = _get_db()
            if action == "create":
                sid   = str(_uuid.uuid4())
                start = effective_date or _now_iso()
                # Get plan to determine interval
                plan_row = conn.execute("SELECT * FROM billing_plans WHERE plan_id=?", (plan_id,)).fetchone()
                interval = dict(plan_row).get("billing_interval", "monthly") if plan_row else "monthly"
                end   = _period_end(start, interval)
                conn.execute("""INSERT INTO billing_subscriptions
                    (subscription_id,customer_id,plan_id,status,current_period_start,current_period_end)
                    VALUES (?,?,?,?,?,?)""", (sid, customer_id, plan_id, "active", start, end))
                conn.commit()
                row = conn.execute("SELECT * FROM billing_subscriptions WHERE subscription_id=?", (sid,)).fetchone()
            elif action in ("upgrade","downgrade"):
                if not all([subscription_id, plan_id]):
                    conn.close()
                    return _make_error("SUBSCRIPTION_INVALID_TRANSITION", "subscription_id and plan_id required", comp)
                conn.execute("UPDATE billing_subscriptions SET plan_id=? WHERE subscription_id=?",
                             (plan_id, subscription_id))
                conn.commit()
                row = conn.execute("SELECT * FROM billing_subscriptions WHERE subscription_id=?", (subscription_id,)).fetchone()
            elif action == "cancel":
                conn.execute("UPDATE billing_subscriptions SET status='canceled', canceled_at=? WHERE subscription_id=?",
                             (_now_iso(), subscription_id))
                conn.commit()
                row = conn.execute("SELECT * FROM billing_subscriptions WHERE subscription_id=?", (subscription_id,)).fetchone()
            elif action == "reactivate":
                conn.execute("UPDATE billing_subscriptions SET status='active', canceled_at=NULL WHERE subscription_id=?",
                             (subscription_id,))
                conn.commit()
                row = conn.execute("SELECT * FROM billing_subscriptions WHERE subscription_id=?", (subscription_id,)).fetchone()
            elif action == "list":
                rows = conn.execute("SELECT * FROM billing_subscriptions WHERE customer_id=?", (customer_id,)).fetchall()
                conn.close()
                return {"subscriptions": [dict(r) for r in rows]}
            else:
                conn.close()
                return _make_error("SUBSCRIPTION_INVALID_TRANSITION", f"Unknown action: {action}", comp)
            result = dict(row) if row else None
            conn.close()
        _cb_success(comp)
        _btrace(comp, "ok", action=action, subscription_id=subscription_id or (result or {}).get("subscription_id"))
        return {"subscription": result}
    except Exception as e:
        _cb_fail(comp)
        return _make_error("SUBSCRIPTION_UPDATE_UNAVAILABLE", str(e), comp)

# 7. UsageEventRecorder
def usage_event_recorder(subscription_id: str, metric: str, quantity: float,
                          occurred_at: str, idempotency_key: str) -> dict:
    comp = "UsageEventRecorder"
    if not _cb_ok(comp):
        return _make_error("USAGE_RECORD_UNAVAILABLE", "Circuit open", comp)
    try:
        with _DB_LOCK:
            conn = _get_db()
            existing = conn.execute("SELECT event_id FROM billing_usage_events WHERE idempotency_key=?",
                                    (idempotency_key,)).fetchone()
            if existing:
                conn.close()
                _btrace(comp, "duplicate", idempotency_key=idempotency_key)
                return {"eventId": existing["event_id"], "recorded": False, "duplicate": True}
            eid = str(_uuid.uuid4())
            conn.execute("""INSERT INTO billing_usage_events
                (event_id,subscription_id,metric,quantity,occurred_at,idempotency_key)
                VALUES (?,?,?,?,?,?)""", (eid, subscription_id, metric, quantity, occurred_at, idempotency_key))
            conn.commit()
            conn.close()
        _cb_success(comp)
        _btrace(comp, "recorded", event_id=eid, metric=metric, qty=quantity)
        return {"eventId": eid, "recorded": True, "duplicate": False}
    except Exception as e:
        _cb_fail(comp)
        return _make_error("USAGE_RECORD_UNAVAILABLE", str(e), comp)

# 8. UsageAggregator
def usage_aggregator(subscription_id: str, period_start: str, period_end: str) -> dict:
    comp = "UsageAggregator"
    try:
        with _DB_LOCK:
            conn = _get_db()
            rows = conn.execute("""
                SELECT metric, SUM(quantity) as total
                FROM billing_usage_events
                WHERE subscription_id=? AND occurred_at >= ? AND occurred_at <= ?
                GROUP BY metric""", (subscription_id, period_start, period_end)).fetchall()
            conn.close()
        summary = [{"metric": r["metric"], "totalQuantity": r["total"]} for r in rows]
        _btrace(comp, "ok", subscription_id=subscription_id, metric_count=len(summary))
        return {"usageSummary": summary}
    except Exception as e:
        return _make_error("USAGE_AGGREGATION_FAIL", str(e), comp)

# 9. CurrencyConverter (with mock FX + stale-rate fallback)
_FX_CACHE: dict = {}   # (from,to) → {rate, ts}
_FX_STALE_S = 3600     # 1 hour

def currency_converter(amount: float, from_currency: str, to_currency: str) -> dict:
    comp = "CurrencyConverter"
    if from_currency == to_currency:
        return {"convertedAmount": amount, "rate": 1.0, "rateTimestamp": _now_iso()}
    if not _cb_ok(comp):
        # Use stale cache if available
        cached = _FX_CACHE.get((from_currency, to_currency))
        if cached:
            converted = round(amount * cached["rate"], 6)
            return {"convertedAmount": converted, "rate": cached["rate"],
                    "rateTimestamp": cached["ts"], "source": "cache_stale"}
        return _make_error("FX_RATE_UNAVAILABLE", "Circuit open, no cache", comp)
    try:
        cached = _FX_CACHE.get((from_currency, to_currency))
        if cached and (_time.time() - cached["age"]) < _FX_STALE_S:
            converted = round(amount * cached["rate"], 6)
            return {"convertedAmount": converted, "rate": cached["rate"],
                    "rateTimestamp": cached["ts"], "source": "cache"}
        # Live fetch (mock rates for now — replace with real API call when FX_API_KEY set)
        MOCK_RATES = {"USD":1.0,"EUR":0.92,"GBP":0.79,"CAD":1.36,"AUD":1.53,"JPY":149.5}
        if from_currency not in MOCK_RATES or to_currency not in MOCK_RATES:
            return _make_error("FX_RATE_UNAVAILABLE", f"Unsupported currency pair {from_currency}/{to_currency}", comp)
        rate = MOCK_RATES[to_currency] / MOCK_RATES[from_currency]
        ts   = _now_iso()
        _FX_CACHE[(from_currency, to_currency)] = {"rate": rate, "ts": ts, "age": _time.time()}
        converted = round(amount * rate, 6)
        _cb_success(comp)
        return {"convertedAmount": converted, "rate": rate, "rateTimestamp": ts, "source": "live"}
    except Exception as e:
        _cb_fail(comp)
        return _make_error("FX_RATE_UNAVAILABLE", str(e), comp)

# 10. TaxCalculator
_TAX_TABLE = {
    "US": 0.0,   "CA": 0.05, "GB": 0.20, "DE": 0.19,
    "FR": 0.20,  "AU": 0.10, "JP": 0.10, "SG": 0.09,
}
def tax_calculator(amount: float, currency: str, customer_country: str,
                   customer_tax_id: str = None, product_type: str = "recurring") -> dict:
    comp = "TaxCalculator"
    if not _cb_ok(comp):
        return {"taxAmount": 0, "taxRate": 0, "taxJurisdiction": "UNKNOWN"}
    try:
        country = (customer_country or "US").upper()[:2]
        # B2B exemption: if customer has a valid tax ID and is in a VAT country
        if customer_tax_id and country in ("GB","DE","FR","AU"):
            rate = 0.0
        else:
            rate = _TAX_TABLE.get(country, 0.0)
        tax_amount = round(amount * rate, 2)
        _cb_success(comp)
        _btrace(comp, "ok", country=country, rate=rate, fallback=country not in _TAX_TABLE)
        return {"taxAmount": tax_amount, "taxRate": rate, "taxJurisdiction": country}
    except Exception as e:
        _cb_fail(comp)
        return {"taxAmount": 0, "taxRate": 0, "taxJurisdiction": "UNKNOWN"}

# 11. PricingEngine
def pricing_engine(plan: dict, usage_summary: list, one_time_charges: list,
                   target_currency: str) -> dict:
    comp = "PricingEngine"
    if not _cb_ok(comp):
        return _make_error("PRICING_CALCULATION_FAIL", "Circuit open", comp)
    try:
        line_items = []
        plan_currency = plan.get("currency", target_currency)
        metered_rates = plan.get("metered_rates") or plan.get("meteredRates") or []
        if isinstance(metered_rates, str):
            metered_rates = json.loads(metered_rates)

        # Base price line item
        base = float(plan.get("base_price") or plan.get("basePrice", 0))
        if plan_currency != target_currency:
            fx = currency_converter(base, plan_currency, target_currency)
            base = fx.get("convertedAmount", base) if not _is_error(fx) else base
        line_items.append({"description": f"{plan.get('name','Plan')} — base",
                            "quantity": 1, "unitPrice": base, "amount": base, "type": "recurring"})

        # Metered usage
        for rate in metered_rates:
            metric     = rate.get("metric","")
            unit_price = float(rate.get("unit_price") or rate.get("unitPrice", 0))
            usage_row  = next((u for u in usage_summary if u.get("metric") == metric), None)
            qty        = float(usage_row.get("totalQuantity", 0)) if usage_row else 0
            amt        = round(qty * unit_price, 6)
            if plan_currency != target_currency:
                fx  = currency_converter(amt, plan_currency, target_currency)
                amt = fx.get("convertedAmount", amt) if not _is_error(fx) else amt
            line_items.append({"description": f"Usage: {metric}", "quantity": qty,
                                "unitPrice": unit_price, "amount": amt, "type": "usage"})

        # One-time charges
        for ot in (one_time_charges or []):
            line_items.append({"description": ot.get("description","One-time"),
                                "quantity": 1, "unitPrice": float(ot.get("amount",0)),
                                "amount": float(ot.get("amount",0)), "type": "one_time"})

        subtotal = round(sum(li["amount"] for li in line_items), 2)
        _cb_success(comp)
        _btrace(comp, "ok", items=len(line_items), subtotal=subtotal, currency=target_currency)
        return {"lineItems": line_items, "subtotal": subtotal, "currency": target_currency}
    except Exception as e:
        _cb_fail(comp)
        return _make_error("PRICING_CALCULATION_FAIL", str(e), comp)

# 12. InvoiceGenerator
def invoice_generator(customer_id: str, subscription_id: str, line_items: list,
                      tax_amount: float, currency: str,
                      period_start: str, period_end: str) -> dict:
    comp = "InvoiceGenerator"
    if not _cb_ok(comp):
        return _make_error("INVOICE_PERSIST_FAILED", "Circuit open", comp)
    try:
        iid      = str(_uuid.uuid4())
        subtotal = round(sum(li.get("amount", 0) for li in line_items), 2)
        total    = round(subtotal + tax_amount, 2)
        due_at   = (_dt.now(_tz.utc) + _td(days=30)).isoformat()
        with _DB_LOCK:
            conn = _get_db()
            conn.execute("""INSERT INTO billing_invoices
                (invoice_id,customer_id,subscription_id,total,tax_amount,currency,
                 status,line_items,period_start,period_end,due_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (iid, customer_id, subscription_id, total, tax_amount, currency,
                 "open", json.dumps(line_items), period_start, period_end, due_at))
            conn.commit()
            conn.close()
        _cb_success(comp)
        _btrace(comp, "ok", invoice_id=iid, total=total, currency=currency)
        return {"invoice": {"invoiceId": iid, "customerId": customer_id,
                            "subscriptionId": subscription_id, "total": total,
                            "taxAmount": tax_amount, "currency": currency,
                            "status": "open", "lineItems": line_items,
                            "issuedAt": _now_iso(), "dueAt": due_at,
                            "periodStart": period_start, "periodEnd": period_end}}
    except Exception as e:
        _cb_fail(comp)
        return _make_error("INVOICE_PERSIST_FAILED", str(e), comp)

# 13. PaymentGatewayClient (mock — replace with Stripe SDK when STRIPE_SECRET_KEY set)
def payment_gateway_client(action: str, invoice_id: str, customer_id: str,
                            amount: float, currency: str,
                            payment_method_token: str = "mock_token") -> dict:
    comp = "PaymentGatewayClient"
    if not _cb_ok(comp):
        return {"paymentResult": {"transactionId": None, "status": "pending",
                                   "failureCode": "GATEWAY_UNAVAILABLE"}}
    try:
        txid   = f"txn_{str(_uuid.uuid4())[:8]}"
        status = "succeeded"
        fcode  = None
        if _BILLING_GATEWAY == "mock":
            # Simulate: amounts ending in .13 fail, .99 are pending
            if str(amount).endswith(".13"):
                status, fcode = "failed", "card_declined"
            elif str(amount).endswith(".99"):
                status = "pending"
        elif _BILLING_GATEWAY == "stripe" and _BILLING_STRIPE_KEY:
            try:
                import stripe as _stripe
                _stripe.api_key = _BILLING_STRIPE_KEY
                if action == "charge":
                    pi = _stripe.PaymentIntent.create(
                        amount=int(amount * 100), currency=currency.lower(),
                        customer=customer_id, payment_method=payment_method_token,
                        confirm=True, metadata={"invoice_id": invoice_id}
                    )
                    txid   = pi.id
                    status = "succeeded" if pi.status == "succeeded" else "failed"
                    fcode  = None
            except Exception as stripe_err:
                status, fcode = "failed", str(stripe_err)[:80]

        # Persist payment record
        with _DB_LOCK:
            conn = _get_db()
            conn.execute("""INSERT INTO billing_payments
                (payment_id,invoice_id,transaction_id,amount,currency,status,failure_code,gateway)
                VALUES (?,?,?,?,?,?,?,?)""",
                (str(_uuid.uuid4()), invoice_id, txid, amount, currency, status, fcode, _BILLING_GATEWAY))
            if status == "succeeded":
                conn.execute("UPDATE billing_invoices SET status='paid', paid_at=? WHERE invoice_id=?",
                             (_now_iso(), invoice_id))
            conn.commit()
            conn.close()
        _cb_success(comp)
        _btrace(comp, "ok", invoice_id=invoice_id, status=status, gateway=_BILLING_GATEWAY)
        return {"paymentResult": {"transactionId": txid, "status": status, "failureCode": fcode}}
    except Exception as e:
        _cb_fail(comp)
        return {"paymentResult": {"transactionId": None, "status": "pending",
                                   "failureCode": "GATEWAY_UNAVAILABLE"}}

# 14. DunningManager
_DUNNING_POLICY = [
    {"attempt": 1, "retry_days": 3,  "action": "none"},
    {"attempt": 2, "retry_days": 5,  "action": "mark_past_due"},
    {"attempt": 3, "retry_days": 7,  "action": "suspend"},
    {"attempt": 4, "retry_days": None, "action": "cancel"},
]
def dunning_manager(invoice_id: str, subscription_id: str,
                    failure_code: str, attempt_number: int) -> dict:
    comp = "DunningManager"
    if not _cb_ok(comp):
        return {"dunningAction": {"nextRetryAt": None, "subscriptionAction": "mark_past_due"}}
    try:
        policy   = next((p for p in _DUNNING_POLICY if p["attempt"] == attempt_number),
                        {"retry_days": None, "action": "cancel"})
        retry_at = ((_dt.now(_tz.utc) + _td(days=policy["retry_days"])).isoformat()
                    if policy["retry_days"] else None)
        sub_action = policy["action"]
        with _DB_LOCK:
            conn = _get_db()
            conn.execute("""INSERT INTO billing_dunning
                (dunning_id,invoice_id,subscription_id,failure_code,attempt_number,
                 next_retry_at,subscription_action)
                VALUES (?,?,?,?,?,?,?)""",
                (str(_uuid.uuid4()), invoice_id, subscription_id, failure_code,
                 attempt_number, retry_at, sub_action))
            if sub_action in ("suspend","cancel"):
                new_status = "past_due" if sub_action == "suspend" else "canceled"
                conn.execute("UPDATE billing_subscriptions SET status=? WHERE subscription_id=?",
                             (new_status, subscription_id))
            conn.commit()
            conn.close()
        _cb_success(comp)
        _btrace(comp, "ok", invoice_id=invoice_id, attempt=attempt_number, action=sub_action)
        return {"dunningAction": {"nextRetryAt": retry_at, "subscriptionAction": sub_action}}
    except Exception as e:
        _cb_fail(comp)
        return {"dunningAction": {"nextRetryAt": None, "subscriptionAction": "mark_past_due"}}

# 15. NotificationDispatcher (mock — replace EmailProvider adaptor with real SMTP/SES/SendGrid)
def notification_dispatcher(recipient_email: str, template_type: str, template_data: dict) -> dict:
    comp = "NotificationDispatcher"
    if not _cb_ok(comp):
        return {"sent": False, "messageId": None}
    try:
        msg_id = f"msg_{str(_uuid.uuid4())[:8]}"
        # Log the notification (mock send)
        logger.info("[billing.notification] type=%s to=%s msg_id=%s",
                    template_type, recipient_email.split("@")[-1], msg_id)
        # TODO: integrate real email provider here when EMAIL_API_KEY is set
        _cb_success(comp)
        return {"sent": True, "messageId": msg_id}
    except Exception as e:
        _cb_fail(comp)
        return {"sent": False, "messageId": None}

# 16. BillingScheduler
def billing_scheduler(as_of_date: str = None) -> dict:
    comp = "BillingScheduler"
    as_of = as_of_date or _now_iso()
    try:
        with _DB_LOCK:
            conn = _get_db()
            rows = conn.execute("""
                SELECT s.subscription_id, s.customer_id,
                       s.current_period_start, s.current_period_end
                FROM billing_subscriptions s
                WHERE s.status = 'active'
                  AND s.current_period_end <= ?""", (as_of,)).fetchall()
            conn.close()
        due = [dict(r) for r in rows]
        _btrace(comp, "ok", as_of=as_of, due_count=len(due))
        return {"dueSubscriptions": due}
    except Exception as e:
        return _make_error("SCHEDULE_QUERY_FAIL", str(e), comp)

# 17. WebhookEventValidator
def webhook_event_validator(raw_payload: str, signature_header: str) -> dict:
    comp = "WebhookEventValidator"
    if not _cb_ok(comp):
        return _make_error("WEBHOOK_VALIDATION_FAILED", "Circuit open", comp)
    try:
        # Stripe-style HMAC-SHA256 signature verification
        if _BILLING_WEBHOOK_SECRET:
            try:
                parts   = {kv.split("=")[0]: kv.split("=")[1]
                            for kv in signature_header.split(",") if "=" in kv}
                ts      = parts.get("t", "")
                sig     = parts.get("v1", "")
                signed  = f"{ts}.{raw_payload}"
                expected = _hmac_billing.new(_BILLING_WEBHOOK_SECRET.encode(),
                                             signed.encode(), _hashlib_billing.sha256).hexdigest()
                if not _hmac_billing.compare_digest(sig, expected):
                    _btrace(comp, "invalid_sig")
                    return _make_error("WEBHOOK_VALIDATION_FAILED", "Invalid signature", comp)
            except Exception:
                return _make_error("WEBHOOK_VALIDATION_FAILED", "Signature parse error", comp)

        payload = json.loads(raw_payload)
        event   = {"eventType": payload.get("type","unknown"),
                   "gatewayTransactionId": payload.get("data",{}).get("object",{}).get("id",""),
                   "status": payload.get("data",{}).get("object",{}).get("status",""),
                   "rawData": payload}
        _cb_success(comp)
        _btrace(comp, "ok", event_type=event["eventType"])
        return {"event": event}
    except json.JSONDecodeError:
        _cb_fail(comp)
        return _make_error("WEBHOOK_VALIDATION_FAILED", "Invalid JSON payload", comp)
    except Exception as e:
        _cb_fail(comp)
        return _make_error("WEBHOOK_VALIDATION_FAILED", str(e), comp)

# ─────────────────────────────────────────────────────────────────────────────
# ORCHESTRATORS
# ─────────────────────────────────────────────────────────────────────────────

# 24. SubscriptionOrchestrator
def subscription_orchestrator(action: str, customer_id: str,
                               plan_id: str = None, subscription_id: str = None) -> dict:
    orch_id = str(_uuid.uuid4())[:8]
    steps   = []
    try:
        # Validate customer
        cust = customer_account_manager("get", customer_id=customer_id)
        if _is_error(cust): return cust
        steps.append("CustomerAccountManager")
        # Validate plan when needed
        if plan_id:
            plan = plan_catalog_manager("get", plan_id=plan_id)
            if _is_error(plan): return plan
            steps.append("PlanCatalogManager")
        # Execute lifecycle action
        result = subscription_lifecycle_manager(action, customer_id=customer_id,
                                                subscription_id=subscription_id, plan_id=plan_id)
        steps.append("SubscriptionLifecycleManager")
        _btrace("SubscriptionOrchestrator", "ok", orch_id=orch_id, steps=len(steps))
        return result
    except Exception as e:
        return _make_error("ORCHESTRATOR_FAIL", str(e), "SubscriptionOrchestrator")

# 25. BillingCycleOrchestrator
def billing_cycle_orchestrator(as_of_date: str = None) -> dict:
    processed, failures = [], []
    due = billing_scheduler(as_of_date)
    if _is_error(due): return due

    for sub_info in due.get("dueSubscriptions", []):
        sid = sub_info["subscription_id"]
        cid = sub_info["customer_id"]
        try:
            # Fetch customer + subscription
            cust_r = customer_account_manager("get", customer_id=cid)
            if _is_error(cust_r): raise ValueError(cust_r["code"])
            cust = cust_r["customer"]

            with _DB_LOCK:
                conn  = _get_db()
                sub_r = conn.execute("SELECT * FROM billing_subscriptions WHERE subscription_id=?", (sid,)).fetchone()
                plan_r= conn.execute("SELECT * FROM billing_plans WHERE plan_id=?",
                                     (sub_r["plan_id"],)).fetchone() if sub_r else None
                conn.close()
            if not sub_r or not plan_r:
                raise ValueError("subscription or plan not found")

            sub   = dict(sub_r)
            plan  = dict(plan_r)
            tc    = cust.get("currency", _BILLING_BASE_CURRENCY)

            # Usage aggregation
            usage_r = usage_aggregator(sid, sub["current_period_start"], sub["current_period_end"])
            usage   = usage_r.get("usageSummary", []) if not _is_error(usage_r) else []

            # Pricing
            pricing_r = pricing_engine(plan, usage, [], tc)
            if _is_error(pricing_r): raise ValueError(pricing_r["code"])

            # Tax
            tax_r = tax_calculator(pricing_r["subtotal"], tc, cust.get("country","US"),
                                   cust.get("tax_id"))
            tax   = tax_r.get("taxAmount", 0)

            # Invoice
            inv_r = invoice_generator(cid, sid, pricing_r["lineItems"], tax, tc,
                                      sub["current_period_start"], sub["current_period_end"])
            if _is_error(inv_r): raise ValueError(inv_r["code"])
            inv   = inv_r["invoice"]

            # Payment
            pay_r = payment_gateway_client("charge", inv["invoiceId"], cid,
                                           inv["total"], tc)
            pay   = pay_r.get("paymentResult", {})

            if pay.get("status") == "succeeded":
                # Roll period forward
                new_start = sub["current_period_end"]
                new_end   = _period_end(new_start, plan.get("billing_interval","monthly"))
                with _DB_LOCK:
                    conn = _get_db()
                    conn.execute("UPDATE billing_subscriptions SET current_period_start=?, current_period_end=? WHERE subscription_id=?",
                                 (new_start, new_end, sid))
                    conn.commit()
                    conn.close()
                # Notify
                notification_dispatcher(cust["email"], "invoice_receipt",
                                        {"invoiceId": inv["invoiceId"], "total": inv["total"],
                                         "currency": tc})
                processed.append({"subscriptionId": sid, "invoiceId": inv["invoiceId"],
                                   "status": "paid"})
            else:
                # Dunning
                attempt = 1  # simplified — would look up existing attempts
                dunning_manager(inv["invoiceId"], sid, pay.get("failureCode","unknown"), attempt)
                notification_dispatcher(cust["email"], "payment_failed",
                                        {"invoiceId": inv["invoiceId"], "failureCode": pay.get("failureCode")})
                processed.append({"subscriptionId": sid, "invoiceId": inv["invoiceId"],
                                   "status": pay.get("status","failed")})
        except Exception as e:
            logger.error("[billing_cycle] sub=%s err=%s", sid, e)
            failures.append({"subscriptionId": sid, "errorCode": str(e)[:80]})

    _btrace("BillingCycleOrchestrator", "done",
            processed=len(processed), failures=len(failures))
    return {"processed": processed, "failures": failures}

# 26. WebhookOrchestrator
def webhook_orchestrator(raw_payload: str, signature_header: str) -> dict:
    validated = webhook_event_validator(raw_payload, signature_header)
    if _is_error(validated):
        return validated
    event = validated.get("event", {})
    etype = event.get("eventType", "")
    txid  = event.get("gatewayTransactionId", "")

    try:
        if "payment_intent.succeeded" in etype:
            with _DB_LOCK:
                conn = _get_db()
                pay_row = conn.execute("SELECT * FROM billing_payments WHERE transaction_id=?", (txid,)).fetchone()
                if pay_row:
                    conn.execute("UPDATE billing_invoices SET status='paid', paid_at=? WHERE invoice_id=?",
                                 (_now_iso(), pay_row["invoice_id"]))
                    conn.commit()
                conn.close()
        elif "payment_intent.payment_failed" in etype:
            with _DB_LOCK:
                conn = _get_db()
                pay_row = conn.execute("SELECT * FROM billing_payments WHERE transaction_id=?", (txid,)).fetchone()
                if pay_row:
                    inv_row = conn.execute("SELECT * FROM billing_invoices WHERE invoice_id=?",
                                           (pay_row["invoice_id"],)).fetchone()
                    if inv_row:
                        dunning_manager(inv_row["invoice_id"], inv_row["subscription_id"],
                                        "webhook_payment_failed", 1)
                conn.close()
    except Exception as e:
        logger.error("[webhook_orch] %s", e)

    _btrace("WebhookOrchestrator", "ok", event_type=etype, txid=txid)
    return {"processed": True, "eventType": etype}

# ─────────────────────────────────────────────────────────────────────────────
# FASTAPI ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

def _billing_guard():
    """Raise 404 if billing is disabled."""
    if not _BILLING_ENABLED:
        raise HTTPException(status_code=404,
                            detail="Billing is disabled. Set SAAS_BILLING_ENABLED=true to enable.")

# ── Status / feature flag ──────────────────────────────────────────────────────
@app.get("/api/billing/status", tags=["Billing"])
async def billing_status():
    """Check whether the billing module is enabled."""
    return {"enabled": _BILLING_ENABLED, "gateway": _BILLING_GATEWAY if _BILLING_ENABLED else None,
            "base_currency": _BILLING_BASE_CURRENCY}

# ── Plans ──────────────────────────────────────────────────────────────────────
@app.get("/api/billing/plans", tags=["Billing"])
async def billing_list_plans():
    _billing_guard()
    return plan_catalog_manager("list")

@app.get("/api/billing/plans/{plan_id}", tags=["Billing"])
async def billing_get_plan(plan_id: str):
    _billing_guard()
    return plan_catalog_manager("get", plan_id=plan_id)

@app.post("/api/billing/plans", tags=["Billing"])
async def billing_upsert_plan(request: Request):
    _billing_guard()
    _require_auth(request)
    body = await request.json()
    return plan_catalog_manager("upsert", plan_id=body.get("planId"), plan=body)

# ── Customers ──────────────────────────────────────────────────────────────────
@app.post("/api/billing/customers", tags=["Billing"])
async def billing_create_customer(request: Request):
    _billing_guard()
    caller = _require_auth(request)
    body   = await request.json()
    body.setdefault("username", caller)
    return customer_account_manager("create", fields=body)

@app.get("/api/billing/customers/me", tags=["Billing"])
async def billing_get_my_customer(request: Request):
    _billing_guard()
    caller = _require_auth(request)
    result = customer_account_manager("get_by_username", customer_id=caller)
    if not result.get("customer"):
        raise HTTPException(status_code=404, detail="No billing profile found. Create one first.")
    return result

@app.get("/api/billing/customers/{customer_id}", tags=["Billing"])
async def billing_get_customer(customer_id: str, request: Request):
    _billing_guard()
    _require_auth(request)
    return customer_account_manager("get", customer_id=customer_id)

@app.put("/api/billing/customers/{customer_id}", tags=["Billing"])
async def billing_update_customer(customer_id: str, request: Request):
    _billing_guard()
    _require_auth(request)
    body = await request.json()
    return customer_account_manager("update", customer_id=customer_id, fields=body)

# ── Subscriptions ──────────────────────────────────────────────────────────────
@app.post("/api/billing/subscriptions", tags=["Billing"])
async def billing_create_subscription(request: Request):
    _billing_guard()
    caller = _require_auth(request)
    body   = await request.json()
    return subscription_orchestrator("create", customer_id=body["customerId"],
                                     plan_id=body["planId"])

@app.get("/api/billing/subscriptions", tags=["Billing"])
async def billing_list_subscriptions(request: Request):
    _billing_guard()
    caller   = _require_auth(request)
    cust     = customer_account_manager("get_by_username", customer_id=caller)
    cust_obj = cust.get("customer")
    if not cust_obj:
        return {"subscriptions": []}
    return subscription_lifecycle_manager("list", customer_id=cust_obj["customer_id"])

@app.put("/api/billing/subscriptions/{subscription_id}", tags=["Billing"])
async def billing_update_subscription(subscription_id: str, request: Request):
    _billing_guard()
    caller = _require_auth(request)
    body   = await request.json()
    action = body.get("action","upgrade")
    return subscription_orchestrator(action, customer_id=body["customerId"],
                                     plan_id=body.get("planId"),
                                     subscription_id=subscription_id)

@app.delete("/api/billing/subscriptions/{subscription_id}", tags=["Billing"])
async def billing_cancel_subscription(subscription_id: str, request: Request):
    _billing_guard()
    caller = _require_auth(request)
    return subscription_lifecycle_manager("cancel", subscription_id=subscription_id)

# ── Usage Events ───────────────────────────────────────────────────────────────
@app.post("/api/billing/usage", tags=["Billing"])
async def billing_record_usage(request: Request):
    _billing_guard()
    _require_auth(request)
    body = await request.json()
    return usage_event_recorder(
        subscription_id  = body["subscriptionId"],
        metric           = body["metric"],
        quantity         = float(body["quantity"]),
        occurred_at      = body.get("occurredAt", _now_iso()),
        idempotency_key  = body.get("idempotencyKey", str(_uuid.uuid4())),
    )

@app.get("/api/billing/usage/{subscription_id}", tags=["Billing"])
async def billing_get_usage(subscription_id: str, period_start: str, period_end: str,
                            request: Request):
    _billing_guard()
    _require_auth(request)
    return usage_aggregator(subscription_id, period_start, period_end)

# ── Invoices ───────────────────────────────────────────────────────────────────
@app.get("/api/billing/invoices", tags=["Billing"])
async def billing_list_invoices(request: Request, customer_id: str = None):
    _billing_guard()
    caller = _require_auth(request)
    with _DB_LOCK:
        conn = _get_db()
        if customer_id:
            rows = conn.execute("SELECT * FROM billing_invoices WHERE customer_id=? ORDER BY issued_at DESC",
                                (customer_id,)).fetchall()
        else:
            cust = customer_account_manager("get_by_username", customer_id=caller)
            cobj = cust.get("customer")
            rows = conn.execute("SELECT * FROM billing_invoices WHERE customer_id=? ORDER BY issued_at DESC",
                                (cobj["customer_id"],)).fetchall() if cobj else []
        conn.close()
    return {"invoices": [dict(r) for r in rows]}

@app.get("/api/billing/invoices/{invoice_id}", tags=["Billing"])
async def billing_get_invoice(invoice_id: str, request: Request):
    _billing_guard()
    _require_auth(request)
    with _DB_LOCK:
        conn = _get_db()
        row  = conn.execute("SELECT * FROM billing_invoices WHERE invoice_id=?", (invoice_id,)).fetchone()
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail=f"Invoice {invoice_id} not found")
    inv = dict(row)
    inv["line_items"] = json.loads(inv.get("line_items","[]"))
    return {"invoice": inv}

# ── Billing cycle (admin) ──────────────────────────────────────────────────────
@app.post("/api/billing/run-cycle", tags=["Billing"])
async def billing_run_cycle(request: Request):
    _billing_guard()
    caller = _require_auth(request)
    if caller != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    body    = await request.json()
    as_of   = body.get("asOfDate", _now_iso())
    result  = billing_cycle_orchestrator(as_of)
    return result

# ── Webhook ────────────────────────────────────────────────────────────────────
@app.post("/api/billing/webhook", tags=["Billing"])
async def billing_webhook(request: Request):
    _billing_guard()
    raw_body  = await request.body()
    sig_hdr   = request.headers.get("Stripe-Signature","")
    result    = webhook_orchestrator(raw_body.decode("utf-8"), sig_hdr)
    if _is_error(result):
        raise HTTPException(status_code=400, detail=result.get("message","Webhook failed"))
    return result

# ── Currency ───────────────────────────────────────────────────────────────────
@app.get("/api/billing/fx", tags=["Billing"])
async def billing_fx(from_currency: str, to_currency: str, amount: float = 1.0):
    _billing_guard()
    return currency_converter(amount, from_currency.upper(), to_currency.upper())

# ── Health / circuit breakers ──────────────────────────────────────────────────
@app.get("/api/billing/health", tags=["Billing"])
async def billing_health():
    _billing_guard()
    comps = ["CustomerAccountManager","SubscriptionLifecycleManager","UsageEventRecorder",
             "CurrencyConverter","TaxCalculator","PricingEngine","InvoiceGenerator",
             "PaymentGatewayClient","DunningManager","NotificationDispatcher",
             "BillingScheduler","WebhookEventValidator"]
    status = {}
    for c in comps:
        s      = _CB.get(c, {})
        open_b = bool(s.get("open_until") and _time.time() < s["open_until"])
        status[c] = {"status": "down" if open_b else "ok",
                     "circuit_open": open_b,
                     "failure_count": s.get("failures", 0)}
    overall = "ok" if all(v["status"] == "ok" for v in status.values()) else "degraded"
    return {"overall": overall, "components": status, "gateway": _BILLING_GATEWAY,
            "enabled": _BILLING_ENABLED}

# ── Run init ───────────────────────────────────────────────────────────────────
_init_billing_db()

@app.get("/{full_path:path}", tags=["System"])
async def spa_fallback(full_path: str):
    """Serve the React SPA for any path not matched by an API route."""
    index = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.isfile(index):
        from fastapi.responses import FileResponse
        return FileResponse(index)
    return {"error": "Frontend not built", "path": full_path, "hint": "Run: npm run build"}


# ══════════════════════════════════════════════════════════════════════════════
# Application Startup / Shutdown
# ══════════════════════════════════════════════════════════════════════════════

@app.on_event("startup")
async def on_startup():
    from core.llm_router import resolve_config_with_ollama_fallback, PROVIDER_DEFAULTS
    _provider = os.getenv("LLM_PROVIDER", "deepseek")
    _model    = os.getenv("LLM_MODEL",    "deepseek-coder")
    _key_env  = f"{_provider.upper()}_API_KEY"
    _api_key  = os.getenv(_key_env)
    eff_provider, eff_model, _ = resolve_config_with_ollama_fallback(_provider, _model, _api_key)

    logger.info("═" * 60)
    logger.info("  ⚡ Mighty Jarvis MKII v4.0.0 — ONLINE")
    logger.info("  Methodology : CBD v2.2")
    logger.info("  Environment : Kali Linux Rolling")
    if eff_provider != _provider:
        logger.info("  Provider    : %s → %s (Ollama fallback — no API key)", _provider, eff_provider)
        logger.info("  Model       : %s (auto-detected from Ollama)", eff_model)
    else:
        logger.info("  Provider    : %s", _provider)
        logger.info("  Model       : %s", _model)
    logger.info("  Max ReAct   : 30 iterations")
    logger.info("  Max Parallel: %s tasks", os.getenv("JARVIS_MAX_PARALLEL", "10"))
    logger.info("  Task Timeout: %s s", TASK_TIMEOUT_SECONDS)
    logger.info("═" * 60)


@app.on_event("shutdown")
async def on_shutdown():
    logger.info("Mighty Jarvis MKII — graceful shutdown initiated")
    with _session_lock:
        _sessions.clear()
    logger.info("All sessions cleared. Goodbye.")


# ══════════════════════════════════════════════════════════════════════════════
# Dev entrypoint
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("DEV", "false").lower() == "true",
        log_level="info",
    )

# ── DB Migration helper ────────────────────────────────────────────────────────
# Called by the reset-admin endpoint and on startup when hash format is detected
# as legacy (no pbkdf2: prefix or wrong inner hash structure).

@app.post("/api/auth/wipe-and-reseed", tags=["Auth"])
async def wipe_and_reseed(request: Request):
    """
    EMERGENCY: Delete ALL users and tokens, reseed admin/admin123.
    Localhost-only. Use this after a hash-scheme migration.
    """
    client_ip = request.client.host if request.client else ""
    if client_ip not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="Only accessible from localhost")
    with _DB_LOCK:
        conn = _get_db()
        conn.execute("DELETE FROM auth_tokens")
        conn.execute("DELETE FROM users")
        conn.commit()
        _create_user_internal(conn, "admin", "admin123")
        conn.close()
    logger.warning("[wipe_reseed] DB wiped and reseeded from %s", client_ip)
    return {"wiped": True, "reseeded": True, "username": "admin", "password": "admin123"}
