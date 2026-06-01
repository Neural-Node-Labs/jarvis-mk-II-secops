# ◈ AI Agent

A skill-based AI agent — Python/FastAPI backend + React web UI.
Default LLM: **DeepSeek**. Also supports Ollama/Llama (local), Anthropic, OpenAI.

---

## 🐳 Docker Quick Start (3 commands)

```bash
# 1. Copy env template and add your key
cp .env.example .env
echo "DEEPSEEK_API_KEY=sk-..." >> .env

# 2. Build and start
docker compose up -d

# 3. Open
open http://localhost:3000
```

---

## Docker Commands

```bash
make setup         # First-time: copy .env.example → .env
make up            # Start everything
make down          # Stop everything
make rebuild       # Force rebuild (no cache)
make logs          # Follow all logs
make logs-backend  # Backend logs only
make ps            # Container status + health
make shell-backend # Shell into backend container
```

---

## Ollama / Local Llama (optional)

1. Uncomment the `ollama:` service in `docker-compose.yml`
2. `docker compose up -d`
3. `make pull-llama3`
4. Switch to Ollama in the UI **⚙ CONFIG** panel

For NVIDIA GPU: also uncomment the `deploy:` block in `docker-compose.yml`.

---

## Configuration (.env)

```bash
DEEPSEEK_API_KEY=sk-...        # Default provider
ANTHROPIC_API_KEY=sk-ant-...   # Optional
OPENAI_API_KEY=sk-...          # Optional
PORT=3000                       # Web UI port
LOG_LEVEL=INFO
HOST_MOUNT=/                    # Host path for filesystem skill
                                # Scope down for safer deploys: ./workspace
```

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                   DOCKER NETWORK: agent-net                  │
│                                                              │
│  ┌──────────────────────────────────┐                        │
│  │  frontend  (nginx :80 → :3000)   │                        │
│  │  • Serves React SPA              │                        │
│  │  • Proxies /api/ and /ws/ →      │                        │
│  │    backend:8000                  │                        │
│  └──────────────┬───────────────────┘                        │
│                 │ proxy                                      │
│  ┌──────────────▼───────────────────┐                        │
│  │  backend  (FastAPI :8000)        │                        │
│  │  • Agent Core + LLM Router       │◄─── /host bind mount   │
│  │  • FileSystem Skill              │                        │
│  │  • OS Execution Skill            │                        │
│  │  • CBD Architect Skill           │                        │
│  └──────────────────────────────────┘                        │
│                                                              │
│  [ ollama :11434 ]  ← optional, uncomment in compose         │
└──────────────────────────────────────────────────────────────┘
```

### File Map

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Service definitions, networks, volumes |
| `.env.example` | Config template — copy to `.env` |
| `Makefile` | `make up/down/logs/rebuild/...` |
| `backend/Dockerfile` | Multi-stage Python 3.12 slim |
| `frontend/Dockerfile` | Node build → nginx alpine |
| `frontend/nginx.conf` | SPA routing + WebSocket proxy + gzip |
| `backend/core/llm_router.py` | Multi-provider streaming LLM |
| `backend/core/agent.py` | Conversation loop + tool-call parser |
| `backend/skills/filesystem_skill.py` | Full host filesystem |
| `backend/skills/os_execution_skill.py` | Shell + process management |
| `backend/skills/cbd_skill.py` | CBD Phase I/II enforcer |
| `frontend/src/App.jsx` | React terminal UI |

---

## Skills

### 📁 filesystem — `write_file`, `delete`, `move` require confirmation
### ⚙️ os_execution — `run_command`, `kill_process`, `send_signal` require confirmation  
### 🏗️ cbd_architect — never assumes, Phase I gate, Phase II one-component gate

---

## Non-Docker (local dev)

```bash
cd backend && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && uvicorn main:app --reload --port 8000

cd frontend && npm install && npm run dev   # http://localhost:5173
```
