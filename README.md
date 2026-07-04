# S.I.R Juan Platform

SIR is a self-contained, Dockerised AI agent platform built for SecOps, pentesting, and autonomous task execution. It pairs a React frontend with a FastAPI backend, a modular skill system, parallel swarm orchestration, and a 9-phase self-evolution pipeline. Multiple LLM providers are supported with automatic Ollama fallback.

---

## Architecture

```
User message
    │
[Attachment + memory injection]
    │
react=False ──────────────────────────────► Quick mode
    │                                        (one LLM turn, tools allowed once)
react=True
    │
force_single_task=True ──────────────────► Deep task
    │                                        (single worker, 30-iteration cap)
    │                                        Used by /api/evolve
    ▼
[1. Classifier] ──CHAT──► Single streamed LLM reply
    │
   TASK
    │
[2. Planner] → DAG of isolated Task objects (≤ 8 parallel)
    │
[3. Swarm]   → asyncio workers, dependency-ordered batches, ReAct per worker
    │
[4. Consolidation] → markdown report → LLM summary → done
```

The central orchestrator is `core/blackbox_brain.py` (`BlackboxBrain`). `core/agent.py` is a thin compatibility re-export so existing call sites work unchanged.

---

## Key Components

### `core/blackbox_brain.py` — Orchestration Engine

Implements the four-layer pipeline above.

| Constant | Default | Purpose |
|---|---|---|
| `DEFAULT_RE_ACT_MAX_LOOP` | 3 | Per-worker ReAct iteration cap (Swarm) |
| `DEEP_TASK_MAX_ITERATIONS` | 30 | Single-worker cap used by `/api/evolve` |
| `DEFAULT_MAX_PARALLEL` | 8 | Max concurrent swarm workers |
| `SWARM_WORKER_TIMEOUT_S` | 300 | Hard per-task timeout (seconds) |
| `MAX_CONVERSATION_TURNS` | 80 | Conversation pruning bound |

Workers communicate through a shared asyncio queue and emit these event types: `token`, `react_status`, `tool_call`, `tool_result`, `confirm_needed`, `done`, `error`.

### `core/llm_router.py` — Provider Abstraction

Normalises DeepSeek, Anthropic, OpenAI-compatible, and Ollama to a single streaming interface. Falls back to Ollama automatically when no API key is configured and a local Ollama instance is reachable. Includes retry logic (up to 2 retries) and per-request tracing via `request_id` headers.

### `core/memory_manager.py` — Episodic Memory

File-backed per-user conversation history stored as Markdown under `./data/memory/`. No external database required. All methods are exception-safe — memory errors never block the agent.

| Method | Description |
|---|---|
| `save_turn(user_id, role, content)` | Append a single turn |
| `save_conversation_block(user_id, user_msg, assistant_msg)` | Append a full exchange |
| `retrieve_last_n(user_id, n)` | Return the last `n` turns |
| `search(user_id, query)` | Full-text search across history |
| `clear(user_id)` | Delete all history for a user |
| `get_stats(user_id)` | Return file size and turn count |

Memory retrieval is triggered automatically when a message matches patterns like "continue where we left off" or "show me the last 5 conversations" (via `detect_retrieval_request()`).

### `core/skill_registry.py` — Skill Dispatch

Discovers, registers, and dispatches skills. Supports both statically-wired skills and dynamic discovery: any `*Skill` class with an `execute` method dropped into `skills/` is picked up automatically — no restart required.

**Built-in skills:**

| Skill | Description |
|---|---|
| `filesystem` | File read/write/delete |
| `os_execution` | Async shell command execution (Kali-native) |
| `file_streamer` | Chunked large-file writes, bypasses LLM token limits |
| `folder_reader` | Directory tree traversal with content summarisation |
| `memory_manager` | Agent-facing memory CRUD wrapper |
| `cbd_architect` | Component-based design tooling and EXP knowledge base |
| `jarvis_mkii` | Parallel task orchestration across isolated agents |
| `swarm` | Multi-agent LLM task decomposition |
| `sentinel` | Autonomous security monitoring and threat response |
| `scheduler` | Scheduled AI calls and shell command execution |
| `selenium_test_skill` | Browser test scaffolding |
| `skill_creator` | Skill evaluation, benchmarking, and packaging |
| `unix_tools_skill` | Unix shell utilities wrapper |

### `skills/os_execution.py` — OS Control

Async subprocess wrapper optimised for Kali Linux security tool execution. Requires explicit confirmation for destructive commands (rm, dd, kill, iptables -F, DROP TABLE, etc.). Sanitises environment variables and shell arguments to prevent injection.

**Actions:** `run_command`, `list_processes`, `kill_process`, `send_signal`, `system_info`, `env_vars`

### `skills/file_streamer.py` — Large File Writes

Streams large file writes through a temporary file that is atomically renamed on finalise. Includes per-stream MD5 verification and a concurrent-open guard. Actions: `start_file`, `append_chunk`, `finalize_file`, `status`, `abort`.

### `skills/folder_reader.py` — Folder Ingestion

Reads entire directory trees (up to 512 KB total, 32 KB per file) with configurable depth, extension filtering, and pattern matching. Returns a structured tree plus file contents for feeding project context to the agent. Actions: `read_tree`, `summarise`.

### `skills/swarm_skill.py` — Swarm Orchestration

Exposes parallel LLM orchestration as a first-class skill using a `ThreadPoolExecutor`. Actions:

| Action | Description |
|---|---|
| `run_full_pipeline` | Orchestrate → swarm → synthesize in one call |
| `orchestrate` | Decompose a task into subtasks via LLM |
| `run_swarm` | Execute subtasks concurrently |
| `synthesize` | Merge results into a final answer |
| `get_status` | List active sessions (auto-pruned after 1 hour) |

### `skills/sentinel_skill.py` — Security Monitoring

Autonomous background monitor that watches log files for threats, analyses them via LLM, and can take automated defensive actions (e.g. blocking IPs via `iptables`). IP validation rejects malformed or octet-leading-zero addresses.

**Actions:** `start_monitoring`, `stop_monitoring`, `get_status`, `analyze_log`, `block_ip`, `unblock_ip`

### `jarvis_mkii_skill.py` — Multi-Agent Parallelism

Fan-out skill that runs N independent tasks simultaneously, each in an isolated `Agent` instance with its own conversation context. Supports per-task abort via asyncio cancellation tokens and returns partial results on timeout.

**Actions:** `run_tasks`, `list_active`, `abort_task`

---

## Frontend — Self-Evolution Dashboard

The React frontend (`frontend/`) includes a dedicated dashboard for the self-evolution pipeline:

| Component | Purpose |
|---|---|
| `EvolutionDashboard` | Root orchestrator; composes all sub-panels |
| `DashboardHeader` | Run / Reset controls |
| `StatsBar` | Live KPIs: workspace ID, files copied, tests passed, EXP entries |
| `PhaseList` / `PhaseItem` | 10-phase pipeline renderer with per-phase status badges |
| `EvolutionLog` | Auto-scrolling timestamped event log |
| `ApprovalGate` | Blueprint review gate (approve / revise) — blocks Phase 5+ until approved |
| `VerdictBanner` | Final pass/fail result with rollback indication |
| `ExpRegistryTable` | EXP knowledge base entries (DRAFT / CONFIRMED) |

---

## API Reference

All endpoints are served at `http://localhost` (port 80 by default). The React SPA handles all other paths via a catch-all fallback.

### System

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Identity card — version, skills list, status |
| `GET` | `/health` | Liveness probe |
| `GET` | `/api/models` | Available models per provider |

### Session

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/session/{user_id}` | Session info and pending confirms |
| `DELETE` | `/api/session/{user_id}` | Destroy and reset session |

### Chat

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/chat` | SSE stream — query params: `message`, `user_id`, `react`, `auto_confirm`, `model`, `provider` |
| `POST` | `/api/chat` | SSE stream — JSON body: same fields |
| `WS` | `/ws/chat/{user_id}` | WebSocket — send `{message, react?, auto_confirm?, model?, provider?, attachments?, halt?}` |
| `POST` | `/api/confirm` | Resume a paused agent — body: `{confirm_id, user_id}` |
| `POST` | `/api/chat/upload` | Upload files as attachments |

### S.I.R Juan Platform (Parallel Tasks)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/mkii/tasks` | Fan out tasks to parallel agents |
| `GET` | `/api/mkii/tasks` | List active running tasks |
| `WS` | `/ws/mkii/{session_id}` | Stream results as each task completes |

### Kali Execution

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/kali/exec` | Direct shell command execution (set `KALI_EXEC_API_KEY` in production) |
| `GET` | `/api/kali/tools` | Tool catalogue by category |

### Memory

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/memory/{user_id}?n=5` | Retrieve last N conversation turns |
| `DELETE` | `/api/memory/{user_id}` | Clear all memory for a user |

### Root Cause Analysis

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/rca` | Trigger RCA pipeline — body: `{symptom, error_message?, environment?, user_id?}` |

### Experienced (EXP Knowledge Base)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/experienced` | List all EXP entries |
| `GET` | `/api/experienced/search?q=&category=&severity=` | Search EXP index |
| `POST` | `/api/experienced/rebuild` | Rebuild EXP index from disk |

### Self-Evolution

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/evolve` | Trigger 9-phase evolution pipeline — body: `{task_description, origin_path, slug, user_id?}` |

### CBD Architect

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/cbd/{action}` | Direct skill invocation — body: `{params: {...}}` |

### Scheduler

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/scheduler/tasks` | List all scheduled tasks |
| `POST` | `/api/scheduler/tasks` | Create a scheduled task |
| `PUT` | `/api/scheduler/tasks/{task_id}` | Update a task |
| `DELETE` | `/api/scheduler/tasks/{task_id}` | Delete a task |
| `POST` | `/api/scheduler/tasks/{task_id}/run` | Run a task immediately |

### Auth

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/auth/login` | Login — returns session token |
| `POST` | `/api/auth/logout` | Invalidate token |
| `POST` | `/api/auth/wipe-and-reseed` | Emergency reset (localhost only) |

---

## Self-Evolution Pipeline

`POST /api/evolve` runs the agent in `force_single_task=True` mode (30-iteration cap) through a 9-phase protocol:

| Phase | Name | Description |
|---|---|---|
| 0 | Experience lookup | Check EXP knowledge base for prior art |
| 1 | Workspace bootstrap | Create isolated workspace directory |
| 2 | Source discovery | Read and map the target skill's source |
| 3 | Analysis | Identify bugs, gaps, and improvement opportunities |
| 4 | Blueprint generation | Write `blueprint.md` + `blueprint.json` |
| — | **Approval gate** | **Human must approve before proceeding** |
| 5 | Implementation | Apply changes from the approved blueprint |
| 6 | Testing | Run tests and validate the implementation |
| 7 | Deploy + Verify | Deploy skill and confirm service health |
| 8 | Experience capture | Write outcome to the EXP knowledge base |

The approval gate (Phase 4 → 5 boundary) is surfaced in the UI via `ApprovalGate.tsx`. The agent streams its reasoning to the client token-by-token throughout.

---

## Getting Started

### Prerequisites

- Docker ≥ 24 and Docker Compose v2
- At least one of: an Anthropic / OpenAI / DeepSeek API key, or a local Ollama instance

### Quick start — cloud LLM

```bash
git clone <repo-url> && cd <repo>
cp .env.example .env
# Set at least one of: ANTHROPIC_API_KEY, OPENAI_API_KEY, DEEPSEEK_API_KEY
docker compose up --build -d
open http://localhost
```

### Quick start — local Ollama (no API key needed)

```bash
docker compose up --build -d
# The compose file pulls qwen2.5-coder:0.5b and creates 'obedient-coder' automatically.
```

When no API key is present and Ollama is reachable, the LLM router falls back automatically. A compact system prompt is used in this mode to fit within the small model's context window.

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | `ollama`, `anthropic`, `openai`, `deepseek` |
| `LLM_MODEL` | `obedient-coder` | Model name for the chosen provider |
| `LIGHTWEIGHT_MODEL` | `1` | Use compact system prompt (for sub-2B models) |
| `ANTHROPIC_API_KEY` | — | Anthropic API key |
| `OPENAI_API_KEY` | — | OpenAI-compatible API key |
| `DEEPSEEK_API_KEY` | — | DeepSeek API key |
| `OLLAMA_HOST` | `http://ollama:11434` | Ollama base URL |
| `PORT` | `80` | Host port for the UI |
| `LOG_LEVEL` | `info` | Logging verbosity |
| `MEMORY_DIR` | `./data/memory` | Per-user memory file directory |
| `MEMORY_MAX_TURNS` | `200` | Max turns retained per user |
| `JARVIS_MAX_PARALLEL` | `10` | Max parallel tasks for S.I.R Juan Platform |
| `JARVIS_TASK_TIMEOUT` | `300` | Per-task timeout in seconds |
| `JARVIS_SCHEDULER_POLL_SECONDS` | `15` | How often the scheduler checks for due tasks |
| `KALI_EXEC_API_KEY` | — | API key guard for `/api/kali/exec` (leave empty only in dev) |
| `KALI_TOOL_TIMEOUT` | `120` | Default shell command timeout |
| `PUID` / `PGID` | `1001` | User/group ID inside the container |
| `HOST_OUTPUT` | `./sir/output` | Host path for agent output files |

---

## Data & Persistence

Three directories are bind-mounted from the host:

| Host path | Container path | Contents |
|---|---|---|
| `./sir/data` | `/app/data` | Provider config (`settings.json`), memory files |
| `./sir/experienced` | `/app/experienced` | EXP knowledge base entries and index |
| `./sir/output` | `/app/output` | Agent-produced output files |

---

## Container Details

Two-stage build:

1. **`frontend-builder`** (`node:20-alpine`) — compiles the React/Vite frontend.
2. **`runtime`** (`kalilinux/kali-rolling`) — nginx (static assets) + uvicorn (FastAPI) under supervisord.

Installed tools: `nmap`, `hydra`, `john`, `sqlmap`, `dnsutils`, `whois`, `chromium`, `chromium-driver`. Python dependencies live in `/opt/venv` (separate from `/app` so bind mounts don't shadow them).

The entrypoint script re-chowns bind-mounted directories on every boot so the unprivileged `sir` user can always write to them.

Logs are written to `system.log` and `llm_interaction.log` inside the container.

---

## Adding Skills

1. Create `skills/my_skill.py` with a class named `MySkill` that has `async def execute(self, action, params, confirmed) -> SkillResult`.
2. Drop the file into the `skills/` directory (bind-mount or rebuild).
3. Call `POST /api/skills/reload` or `registry.reload()` — no container restart required.

For destructive actions, return `SkillResult.confirm(prompt="…")`. The frontend surfaces the confirmation prompt; the operator resumes via `POST /api/confirm`.

`SkillResult` helpers:

```python
SkillResult.ok(output)           # success with payload
SkillResult.fail("ERROR_CODE: message")  # failure
SkillResult.confirm("prompt", output)    # pause for human approval
```
