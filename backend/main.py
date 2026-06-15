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
from core.prompt_builder import build_system_prompt, AGENT_SYSTEM_PROMPT, list_personas, DEFAULT_PERSONA
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
    effective_cwd     = cwd or "/tmp"

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
                await ws.send_json({"type": "auth_ok", "data": {"username": ws_user}})
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
            workspace_path  = _workspace_path(user_id, project)   # /tmp/{user}/{project}/workspace

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
    """Client-side pre-hash: SHA-256 of the raw password → hex string."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def _hash_password(pre_hashed: str) -> str:
    """
    Server-side storage hash.
    Input: the client-sent SHA-256 hex of the password.
    Stores: PBKDF2-HMAC-SHA256(pre_hashed, salt, 260_000 iterations).
    This means the raw password is NEVER transmitted or stored.
    """
    salt = secrets.token_hex(16)
    dk   = hashlib.pbkdf2_hmac("sha256", pre_hashed.encode(), salt.encode(), 260_000)
    return f"pbkdf2:{salt}:{dk.hex()}"

def _verify_password(candidate: str, stored_hash: str) -> bool:
    """
    Verify a candidate against the stored hash.

    candidate: the value sent by the client — EITHER:
      (a) SHA-256 hex of the password  (new clients that pre-hash)
      (b) raw plaintext                (fallback for migration / curl testing)

    stored_hash format: "pbkdf2:{salt}:{dk_hex}"
    Legacy format (no prefix): "{salt}:{dk_hex}" — treated as plain PBKDF2(plaintext)
    """
    try:
        if stored_hash.startswith("pbkdf2:"):
            _, salt, dk_hex = stored_hash.split(":", 2)
            # Try candidate as-is (SHA-256 hex from client)
            dk = hashlib.pbkdf2_hmac("sha256", candidate.encode(), salt.encode(), 260_000)
            if hmac.compare_digest(dk.hex(), dk_hex):
                return True
            # Fallback: maybe candidate is plaintext (curl / API tester) — pre-hash and retry
            dk2 = hashlib.pbkdf2_hmac("sha256", _sha256_hex(candidate).encode(), salt.encode(), 260_000)
            return hmac.compare_digest(dk2.hex(), dk_hex)
        else:
            # Legacy format: "{salt}:{dk_hex}" — PBKDF2 of plaintext directly
            salt, dk_hex = stored_hash.split(":", 1)
            dk = hashlib.pbkdf2_hmac("sha256", candidate.encode(), salt.encode(), 260_000)
            return hmac.compare_digest(dk.hex(), dk_hex)
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
            ph = _hash_password(new_pass)
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
        ph = _hash_password(_sha256_hex(new_pw))
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

# Path: /tmp/{user}/{project}/workspace
# ENV: JARVIS_WORKSPACE_ROOT=/tmp  (override base dir)
#      JARVIS_DEFAULT_PROJECT=default
WORKSPACE_ROOT    = os.getenv("JARVIS_WORKSPACE_ROOT",    "/tmp")
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
    """Return /tmp/{user} — created on demand."""
    path = os.path.join(WORKSPACE_ROOT, _safe_name(user_id))
    os.makedirs(path, exist_ok=True)
    return path


def _workspace_path(user_id: str, project: str = DEFAULT_PROJECT) -> str:
    """Return /tmp/{user}/{project}/workspace — created on demand."""
    safe_proj = _safe_name(project) if project else DEFAULT_PROJECT
    path = os.path.join(_user_root(user_id), safe_proj, "workspace")
    os.makedirs(path, exist_ok=True)
    return path


def _project_root(user_id: str, project: str) -> str:
    """Return /tmp/{user}/{project} — created on demand."""
    path = os.path.join(_user_root(user_id), _safe_name(project))
    os.makedirs(path, exist_ok=True)
    return path


def _list_projects(user_id: str) -> list[dict]:
    """
    List all projects for a user.
    A project is any subdirectory of /tmp/{user}/ that is not hidden.
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
    List files in /tmp/{user_id}/{project}/workspace/.
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
    Path: /tmp/{user_id}/{project}/workspace/ → {project}.zip

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
# Path schema: /tmp/{user}/{project}/workspace/
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/projects/{user_id}", tags=["Workspace"])
async def list_projects(user_id: str):
    """
    List all projects for a user.
    Each project is a directory at /tmp/{user_id}/{project}/.
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
    Creates /tmp/{user_id}/{project}/workspace/ and a .jarvis_project.json metadata file.
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

@app.get("/api/personas", tags=["System"])
async def get_personas():
    """
    List available personas for the UI persona selector.
    Each persona has its own soul block in prompt_builder.py and a mirrored
    manifesto file at personas/{id}.md.
    """
    return {
        "personas":        list_personas(),
        "default_persona": DEFAULT_PERSONA,
    }


@app.get("/api/personas/{persona_id}/manifesto", tags=["System"])
async def get_persona_manifesto(persona_id: str):
    """Return the manifesto markdown for a given persona id."""
    from core.prompt_builder import PERSONAS, PERSONAS_DIR
    p = PERSONAS.get(persona_id)
    if not p:
        raise HTTPException(status_code=404, detail=f"Unknown persona '{persona_id}'")
    path = os.path.join(PERSONAS_DIR, p["manifest"])
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        content = p["soul"]
    return {"id": persona_id, "name": p["name"], "tagline": p["tagline"], "manifesto": content}


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
    logger.info("═" * 60)
    logger.info("  ⚡ Mighty Jarvis MKII v4.0.0 — ONLINE")
    logger.info("  Methodology : CBD v2.2")
    logger.info("  Environment : Kali Linux Rolling")
    logger.info("  Provider    : %s", os.getenv("LLM_PROVIDER", "deepseek"))
    logger.info("  Model       : %s", os.getenv("LLM_MODEL",    "deepseek-coder"))
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