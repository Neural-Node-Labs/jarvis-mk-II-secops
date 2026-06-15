# Blueprint — Mighty Jarvis MKII Server Restructure

**Project ID**: `jarvis-mkii-server-v4`
**Methodology**: CBD-Interface-First v2.2
**Blueprint Version**: 1.0.0
**Status**: APPROVED
**Date**: 2026-06-10

---

## Blueprint Changelog

| Version | Date | Changed By | Summary |
|---|---|---|---|
| 1.0.0 | 2026-06-10 | agent | Initial blueprint — CBD restructure of main.py, prompt_builder.py, and JarvisMKII skill |

---

## Purpose

Restructure the Jarvis MKII server (`main.py`) from a monolithic file into a CBD-compliant
multi-component architecture. Integrate all four blueprint systems (Memory, RCA, Experienced,
Self-Evolution) as first-class citizens. Add JarvisMKII multi-thread parallel task skill.
Remove all Pydantic usage. Retain name, personality, and Kali Linux operational identity.

---

## Component Table

| # | Component Name | Logical Function | IN Schema | OUT Schema | Adaptor | Trace Points | Exp Hook |
|---|---|---|---|---|---|---|---|
| 1 | `LoggingSubsystem` | Dual-stream log init | env (LOG_LEVEL) | Logger objects | FileHandler | system.log, llm_interaction.log | None |
| 2 | `ApplicationBootstrap` | FastAPI + CORS + static | env vars, CORS_ORIGINS | `app` instance | CORSMiddleware, StaticFiles | startup, shutdown | None |
| 3 | `SessionRegistry` | Per-user Agent lifecycle | user_id, model?, provider? | Agent instance | LLMConfig, SkillRegistry | session_created, session_destroyed | Consumer |
| 4 | `JarvisMKIIExecutor` | Parallel task fan-out | { tasks[], max_parallel? } | { submitted, results[] } | asyncio.gather, AgentFactory | task_submitted, task_started, task_complete, task_failed, task_timeout, all_tasks_done | Consumer + Producer |
| 5 | `KaliToolExecutor` | Async subprocess for Kali tools | { command, cwd?, timeout?, env? } | { stdout, stderr, returncode, duration_ms } | asyncio.create_subprocess_shell | kali_exec_start, kali_exec_complete, kali_exec_timeout | Consumer |
| 6 | `SSEStreamBuilder` | Convert Agent events to SSE bytes | AsyncGenerator[dict] | AsyncGenerator[bytes] | None | stream_start, stream_token, stream_done, stream_error | None |
| 7 | `AttachmentProcessor` | Decode multipart uploads | UploadFile[] | attachment dict[] | base64, MIME detection | attachment_processed, attachment_skip | None |
| 8 | `HealthInfoRouter` | System status + version + capability | HTTP GET | JSON | FastAPI router | None | None |
| 9 | `ChatRouter` | SSE + WebSocket + POST chat | { message, user_id, react, auto_confirm, model, provider } | SSE stream | Agent, SSEStreamBuilder | chat_request_received, chat_stream_start, chat_stream_done | Consumer |
| 10 | `FileUploadChatRouter` | Multipart file upload + chat | multipart form | SSE stream | AttachmentProcessor, Agent | chat_upload | Consumer |
| 11 | `ConfirmationGate` | Resume paused ReAct after confirm | { confirm_id, user_id } | SSE stream | Agent.confirm_action | confirm_request, confirm_ok, confirm_not_found | None |
| 12 | `JarvisMKIIRouter` | Multi-task HTTP + WebSocket | { tasks[] } POST or WS | JSON results / WS events | JarvisMKIIExecutor | mkii_submitted, mkii_complete | Consumer + Producer |
| 13 | `KaliExecRouter` | Direct Kali tool HTTP endpoint | { command, cwd?, timeout? } | { stdout, stderr, returncode } | KaliToolExecutor | kali_exec | Consumer |
| 14 | `MemoryRouter` | Conversation history CRUD | user_id, n | history dict | MemoryManager | memory_retrieve, memory_clear | Consumer |
| 15 | `RCARouter` | RCA pipeline trigger | { symptom, error_message?, environment? } | SSE stream | Agent (react=True) | rca_request | Consumer + Producer |
| 16 | `ExperiencedRouter` | EXP knowledge CRUD | query, category?, severity? | search results | cbd_architect skill | exp_list, exp_search, exp_rebuild | Consumer + Producer |
| 17 | `SelfEvolutionRouter` | 9-phase evolution trigger | { task_description, origin_path, slug } | SSE stream | Agent (react=True) | evolve_request | Consumer + Producer |
| 18 | `CBDArchitectRouter` | Direct CBD skill invocations | { action, params } | skill result | cbd_architect skill | cbd_action | Consumer |
| 19 | `SPAFallback` | Serve React frontend | any non-API path | FileResponse | StaticFiles | None | None |
| 20 | `JarvisMKIISkill` | SkillRegistry-registered parallel executor | { tasks[] } via TOOL_CALL | result dict | asyncio, AgentFactory | task_started, task_complete, all_tasks_done | Consumer + Producer |
| 21 | `PromptBuilder` | System prompt assembly with soul + blueprints | memory_context? | system prompt string | None | None | None |

---

## Technical Risk Assessment

### Critical Bottlenecks
1. **JarvisMKIIExecutor** — asyncio.gather with many concurrent Agents can exhaust memory or LLM API rate limits. Mitigation: `JARVIS_MAX_PARALLEL` cap + per-task timeout.
2. **KaliToolExecutor** — long-running tools (nmap large subnets, hashcat) can block the event loop if not fully async. Mitigation: `asyncio.create_subprocess_shell` + `asyncio.wait_for`.
3. **SessionRegistry** — unbounded session accumulation over time. Mitigation: session TTL or explicit `/api/session/{id}` DELETE.

### Integration Friction
- `JarvisMKIISkill` registered in SkillRegistry must receive an `agent_factory` callable injected at startup — circular import risk if not handled via lazy import.
- `SSEStreamBuilder` must flush immediately; nginx buffering can introduce latency. Mitigation: `X-Accel-Buffering: no` header.

### Stability Warnings
- Kali tool stdout can be very large (nmap XML on large subnets). Output must be written to /tmp and summarised, not loaded raw into context.
- WebSocket sessions for JarvisMKII (`/ws/mkii/{session_id}`) must handle mid-stream disconnects — tasks should complete even if the client drops.

### Experience Gaps
- Kali tool timeout tuning per-tool not yet in Experienced system — will generate DRAFT entries on first production runs.

### Feasibility Verdict
**GO** — All components are achievable with standard Python async + FastAPI + Kali Linux toolset.

---

## Adaptor Registry

| Adaptor | Purpose | Used By |
|---|---|---|
| `asyncio.create_subprocess_shell` | Non-blocking Kali tool execution | KaliToolExecutor |
| `asyncio.gather` | Parallel task fan-out | JarvisMKIIExecutor |
| `SSEStreamBuilder` | Agent events → SSE bytes | ChatRouter, RCARouter, SelfEvolutionRouter |
| `MemoryManager` | Conversation persistence | SessionRegistry, MemoryRouter |
| `SkillRegistry` | Skill dispatch | SessionRegistry |
| `LLMConfig` | LLM provider configuration | SessionRegistry, JarvisMKIIExecutor |

---

## Artifact Registry

| File | Component | Version | Description |
|---|---|---|---|
| `main.py` | Components 1–19 | 4.0.0 | CBD-restructured FastAPI server |
| `prompt_builder.py` | Component 21 | 2.0.0 | Soul + personality + blueprint context + rules |
| `jarvis_mkii_skill.py` | Component 20 | 1.0.0 | JarvisMKII parallel task skill |
| `blueprint.md` | All | 1.0.0 | This document |
| `blueprint.json` | All | 1.0.0 | Machine-readable contracts |

---

## Stability & Trace Strategy

| Component | Isolation | Error Format | Log Points |
|---|---|---|---|
| SessionRegistry | Lock-guarded dict | raises HTTPException 500 | session_created, session_destroyed |
| JarvisMKIIExecutor | Per-task asyncio coroutine | result.status = "failed"\|"timeout" | task_started, task_complete, task_failed, task_timeout |
| KaliToolExecutor | Subprocess isolation | result.returncode = -1\|-2 | kali_exec_start, kali_exec_complete, kali_exec_timeout |
| SSEStreamBuilder | try-finally guaranteed | SSE "error" event | stream_error |
| All Routers | per-request try-except | HTTPException 400\|500 | per-component trace points |

---

## CBD Skills Used

| Phase | Skills |
|---|---|
| Phase 0 | cbd_architect.experienced_lookup, cbd_architect.experienced_search |
| Phase II | filesystem, os_execution, file_streamer, jarvis_mkii |
| Phase III | cbd_architect.experienced_capture, cbd_architect.experienced_promote |
| Memory | memory_manager.save, memory_manager.retrieve |
| RCA | All 10 RCA components via agent ReAct loop |
| Evolution | self_evolution skill via agent ReAct loop |
