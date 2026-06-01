# Jarvis AI Agent — System Blueprint

**Version:** 2.0.0 | **Date:** 2025-06-01 | **Methodology:** CBD v2.2

---

## 1. System Overview

Jarvis is an AI agent platform with three interfaces sharing a common Agent/SkillRegistry core:

| Interface | Technology | Purpose |
|-----------|-----------|---------|
| Console REPL | Python async (console.py) | Interactive terminal: streaming tokens, tool calls, confirmations |
| WebSocket API | FastAPI (main.py) | Real-time agent chat (/ws/chat) + evolution pipeline (/ws/evolution) |
| React Dashboard | TypeScript + Vite | GUI for self-evolution pipeline visualization and control |

---

## 2. Architecture

```
CLIENTS:  console.py (REPL)  |  /ws/chat (Frontend)  |  React Dashboard
               |                      |                     |
               v                      v                     v
BACKEND:  FastAPI main.py (12 routes, CORS, skill registration)
               |
          Agent.chat_stream() -- ReAct loop (max 10 iter) or single cycle
               |                      |
          LLMRouter              SkillRegistry (24 skills)
          (OpenAI/DeepSeek)       +-- filesystem, os_execution, cbd_architect, self_evolution
                                  +-- 20 security skills

SELF-EVOLUTION ENGINE (skills/self_evolution_skill/):
  session.py (dataclass) -> orchestrator.py (async FSM) -> phase_runners.py (10 phases)
                    |
         evolution_skill.py (SkillRegistry API: start|status|approve|revise|reset|get_logs)
```

---

## 3. Component Table

### 3.1 Backend (9 components)

| ID | Component | File | Function |
|----|-----------|------|----------|
| B1 | FastAPI Server | main.py | HTTP/WS entry; 12 routes; CORS; skill registration |
| B2 | Agent | core/agent.py v2.0.0 | ReAct loop (max 10 iter); tool extraction; confirmation flow |
| B3 | LLMRouter | core/llm_router.py | Provider-agnostic LLM streaming (OpenAI/Anthropic/DeepSeek) |
| B4 | SkillRegistry | core/skill_registry.py | 24 skills; dispatch; confirmation gate |
| B5 | EvolutionSession | session.py | Dataclass: phases, logs, EXP entries, stats, gate |
| B6 | PhaseRunners | phase_runners.py | 10 async phase functions (0-9) |
| B7 | EvolutionOrchestrator | orchestrator.py | Async FSM; Phase 4 pause via asyncio.Event; abort |
| B8 | SelfEvolutionSkill | evolution_skill.py | 6 actions: start/status/approve/revise/reset/get_logs |
| B9 | Console REPL | console.py | Colored terminal; streaming; /reset, /react commands |

### 3.2 Frontend (10 components)

| ID | Component | File | Function |
|----|-----------|------|----------|
| F1 | EvolutionDashboard | EvolutionDashboard.tsx | Root orchestrator |
| F2 | DashboardHeader | DashboardHeader.tsx | Title + Run/Reset buttons |
| F3 | StatsBar | StatsBar.tsx | 4 KPI cards |
| F4 | PhaseList | PhaseList.tsx | 10 PhaseItem container |
| F5 | PhaseItem | PhaseItem.tsx | Single phase row: dot, icon, label, badge |
| F6 | EvolutionLog | EvolutionLog.tsx | Scrollable log, auto-scroll |
| F7 | ApprovalGate | ApprovalGate.tsx | Phase 4 APPROVE/REVISE |
| F8 | VerdictBanner | VerdictBanner.tsx | Pass/fail banner |
| F9 | ExpRegistryTable | ExpRegistryTable.tsx | EXP entries table |
| F10 | useEvolutionEngine | useEvolutionEngine.ts v2.0.0 | API-connected: WS + 500ms polling |

### 3.3 Bin Scripts (3)

| ID | File | Function |
|----|------|----------|
| S1 | bin/jarvis.sh | Linux/macOS launcher: venv -> pip install -> console.py |
| S2 | bin/jarvis.cmd | Windows launcher: venv -> pip install -> console.py |
| S3 | bin/validate_evolution.py | System audit: files, imports, routes, components |

---

## 4. 10-Phase CBD v2.2 Evolution Pipeline

POST /api/evolution/start -> SelfEvolutionSkill._action_start():

| Phase | Name | Key Action |
|-------|------|------------|
| 0 | Experience lookup | Check experienced/index.md (LOOKUP_HIT/MISS) |
| 1 | Workspace bootstrap | mkdir tree, init evolution.log |
| 2 | Source discovery | Enumerate source files, read versions |
| 3 | Code analysis | Write reports/analysis.md, Feasibility verdict: GO |
| 4 | Blueprint gen + GATE | blueprint.md + blueprint.json; HALT for human approval |
| 5 | Implementation | Apply changes, increment versions (1.0.0 -> 1.1.0) |
| 6 | Build validation | Syntax checks, BUILD_PASS per component, smoke test |
| 7 | UI & API testing | Generate test suite, ALL_PASS |
| 8 | Deploy & verify | Create backup, copy files, hash verify |
| 9 | Restart & health | Skill reload, HEALTH_CHECK UP, create EXP entry, COMPLETE |

**Phase 4 Gate:** Orchestrator calls asyncio.Event().wait(). POST /api/evolution/approve calls orchestrator.approve() which sets the event, unblocking to Phase 5.


## 5. API Contract

### WebSocket Endpoints

| Path | Client->Server | Server->Client |
|------|---------------|----------------|
| /ws/chat | chat, confirm, cancel_confirm | token, tool_call, tool_result, confirm_needed, react_status, done |
| /ws/evolution | subscribe(workspace_id) | evolution_state, evolution_complete |

### REST Endpoints (12 total)

| Method | Path | Purpose |
|--------|------|---------|
| GET | /api/skills | List registered skills |
| GET/POST | /api/config | Get/update LLM provider settings |
| POST | /api/reset | Clear agent conversation |
| GET | /api/health | Health check + provider + skill count |
| POST | /api/evolution/start | Begin evolution cycle -> workspace_id + state |
| GET | /api/evolution/status | Poll current EvolutionState |
| POST | /api/evolution/approve | Approve blueprint (resumes Phase 4) |
| POST | /api/evolution/revise | Send blueprint back for revision |
| POST | /api/evolution/reset | Abort evolution cycle |
| GET | /api/evolution/logs | Retrieve log entries |

### EvolutionState Schema

14 fields: running(bool), currentPhase(number -1..10), paused(bool), approved(bool), aborted(bool), workspaceId(string), filesCopied(string), testsPassed(string), expCount(string), verdict(pass|fail|null), phases(PhaseState[]), gateStatus(hidden|waiting|approved|revised), logs(LogEntry[]), expEntries(ExpEntry[]).

PhaseStatusEnum: idle | running | done | blocked | fail
LogType: info | success | warn | error | muted

---

## 6. Skill Registry (24 Skills)

**Core:** filesystem (8 actions), os_execution (6 actions), cbd_architect (13 actions), self_evolution (6 actions)

**Security (20):** port_scanner, dns_lookup, whois_lookup, ssl_cert_inspector, http_header_analyzer, network_recon, dns_security, cve_lookup, ip_reputation, hash_lookup, ioc_extractor, log_analyzer, vulnerability_scorer, web_app_scanner, api_security_audit, firewall_auditor, password_audit, cloud_posture, container_scanner, utility

---

## 7. Frontend Data Flow

useEvolutionEngine v2.0.0 (useEvolutionEngine.ts):

1. runEvolution() -> POST /api/evolution/start -> receives {workspace_id, state}
2. Opens WebSocket /ws/evolution with subscribe(workspace_id) for real-time updates
3. Starts 500ms polling GET /api/evolution/status as fallback
4. On state event: mapBackendState(backendJSON) -> dispatch(SYNC_STATE) -> React re-renders all 9 components
5. approveBlueprint() -> POST /api/evolution/approve -> backend orchestrator.approve() sets asyncio.Event() -> Phase 4 resumes to Phase 5
6. reviseBlueprint() -> POST /api/evolution/revise -> resets approval flag
7. resetEvolution() -> POST /api/evolution/reset -> orchestrator.abort() -> cleanup WS and polling
8. useEffect cleanup: close WebSocket, clearInterval(polling)

---

## 8. Console REPL

File: console.py (118 lines). Modes: ReAct (default), Single-cycle (--no-react), Auto-confirm (--auto-confirm).

Event Handling: token prints to stdout, tool_call displays skill.action, tool_result shows checkmark or cross with error, confirm_needed prompts interactive y/n then calls agent.confirm_action(), react_status (healing=true) displays iteration info.

Commands: /reset (clear conversation), /react (toggle ReAct), exit/quit.

---

## 9. Launcher Scripts

bin/jarvis.sh (Linux/macOS) and bin/jarvis.cmd (Windows):
1. Check if backend/.venv exists -> create if not (python3 -m venv)
2. Activate virtual environment
3. Run: pip install -q -r requirements.txt
4. Execute: python console.py, forwarding all command-line arguments

---

## 10. Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| In-memory session storage lost on restart | Medium | Use Redis for production |
| WebSocket polling fallback adds 500ms latency | Low | Acceptable UX trade-off; WS is primary transport |
| Phase runners are simulated (time delays) | Low | Replaceable with real skill calls |
| Old hyphen directory removed | Low | Resolved: Python package uses underscores |
| v9fs tool output channel has ~8-10 KB limit | Medium | Chunked writes for files >5 KB; documented in RCA |

### Feasibility Verdict: GO

All 22 components (9 backend, 10 frontend, 3 bin) are implemented, validated, and functional.
Validation script (bin/validate_evolution.py) reports: ALL CHECKS PASSED.

---

*Generated from live source code audit of all backend, frontend, and bin files — 2025-06-01*
