#!/usr/bin/env python3
"""
console.py — Jarvis Console REPL
Professional terminal interface for the Jarvis AI Agent Platform.
All FastAPI endpoints from main.py are exposed as /commands.
"""

import sys
import os
import asyncio
import json
import shutil
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.llm_router import LLMConfig, PROVIDER_DEFAULTS
from core.skill_registry import SkillRegistry
from core.agent import Agent
from skills.filesystem_skill import FileSystemSkill
from skills.os_execution_skill import OSExecutionSkill
from skills.cbd_skill import CBDArchitectSkill


# ─────────────────────────────────────────────────────────────────────────────
#  ANSI COLOR & STYLE PALETTE
# ─────────────────────────────────────────────────────────────────────────────
RESET    = "\033[0m";  BOLD     = "\033[1m";  DIM      = "\033[2m"
ITALIC   = "\033[3m";  ULINE    = "\033[4m"

BLACK    = "\033[30m";  RED      = "\033[31m";  GREEN    = "\033[32m"
YELLOW   = "\033[33m";  BLUE     = "\033[34m";  MAGENTA  = "\033[35m"
CYAN     = "\033[36m";  WHITE    = "\033[37m";  GRAY     = "\033[90m"
BRED     = "\033[91m";  BGREEN   = "\033[92m";  BYELLOW  = "\033[93m"
BBLUE    = "\033[94m";  BMAGENTA = "\033[95m";  BCYAN    = "\033[96m"
BWHITE   = "\033[97m"

BG_BLACK = "\033[40m";  BG_RED   = "\033[41m";  BG_GREEN = "\033[42m"
BG_YELL  = "\033[43m";  BG_BLUE  = "\033[44m";  BG_MAG   = "\033[45m"
BG_CYAN  = "\033[46m";  BG_WHITE = "\033[47m"


def paint(text: str, *codes: str) -> str:
    """Apply ANSI style codes to text, then reset."""
    return f"{''.join(codes)}{text}{RESET}"


def term_width() -> int:
    return min(shutil.get_terminal_size(fallback=(80, 24)).columns, 88)


def divider(char: str = "─", color: str = GRAY) -> str:
    return paint(char * term_width(), color)


def section_bar(label: str, color: str = BCYAN) -> str:
    w = term_width()
    label_str = f"  {label}  "
    pad = w - len(label_str)
    left = pad // 2
    right = pad - left
    return (paint("─" * left, GRAY) + paint(label_str, BOLD, color) + paint("─" * right, GRAY))


def badge(text: str, fg: str = BLACK, bg: str = BG_CYAN) -> str:
    return paint(f" {text} ", BOLD, fg, bg)


def method_badge(method: str) -> str:
    palette = {
        "GET":    (BLACK, BG_GREEN),
        "POST":   (BLACK, BG_CYAN),
        "WS":     (BLACK, BG_MAG),
        "DELETE": (BLACK, BG_RED),
    }
    fg, bg = palette.get(method.upper(), (BLACK, BG_WHITE))
    return paint(f" {method.upper():<6} ", BOLD, fg, bg)


# ─────────────────────────────────────────────────────────────────────────────
#  BANNER
# ─────────────────────────────────────────────────────────────────────────────
def print_banner():
    lines = [
        paint("       ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗", BCYAN, BOLD),
        paint("       ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝", BCYAN, BOLD),
        paint("       ██║███████║██████╔╝██║   ██║██║███████╗", CYAN,  BOLD),
        paint("  ██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║", CYAN,  BOLD),
        paint("  ███████║██║  ██║██║  ██║ ╚████╔╝ ██║███████║", BLUE,  BOLD),
        paint("  ╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝", BLUE,  BOLD),
        paint("       Jarvis Platform  ·  Neural Node Labs   ",    DIM,   WHITE),
    ]
    print()
    for line in lines:
        print(line)
    print()


def print_status_bar(react: bool, auto_confirm: bool, skill_count: int,
                     provider: str, model: str, api_url: str):
    w = term_width()
    r_badge = badge(" REACT ON  ", BLACK, BG_CYAN)  if react        else badge(" REACT OFF ", BLACK, BG_WHITE)
    a_badge = badge(" AUTO ON   ", BLACK, BG_CYAN)  if auto_confirm else badge(" AUTO OFF  ", BLACK, BG_WHITE)
    print(paint("  " + "─" * (w - 2), GRAY))
    print(f"  {r_badge}  {a_badge}"
          f"  {paint(str(skill_count) + ' skills', DIM, BGREEN)}"
          f"  {paint(provider + '/' + model, DIM, GRAY)}"
          f"  {paint(api_url, DIM, BLUE)}")
    print(paint("  " + "─" * (w - 2), GRAY))


# ─────────────────────────────────────────────────────────────────────────────
#  HELP
# ─────────────────────────────────────────────────────────────────────────────
def print_help():
    sections = [
        ("AGENT", [
            ("/reset",                     "Clear conversation history"),
            ("/react",                     "Toggle ReAct reasoning mode on/off"),
            ("/status",                    "Show current runtime configuration"),
            ("/skills",                    "List locally loaded skills"),
            ("/clear",                     "Clear screen and redraw banner"),
            ("chat",                       "Enter standard loop simulation"),
        ]),
        ("WEBSOCKET  ( /ws/* )", [
            ("(live chat)",                "WS /ws/chat  — active during normal chat"),
            ("(evolution feed)",           "WS /ws/evolution  — streamed via /evo status"),
        ]),
        ("CONFIG  ( /api/config )", [
            ("/config",                    "Show current LLM config      GET  /api/config"),
            ("/config set <k>=<v> …",      "Update LLM config            POST /api/config"),
            ("  keys: provider  model  api_key  base_url", ""),
            ("         temperature  max_tokens  schema_format", ""),
        ]),
        ("SKILLS  ( /api/skills )", [
            ("/skills list",               "List skills via API          GET  /api/skills"),
        ]),
        ("HEALTH  ( /api/health )", [
            ("/health",                    "Server health check          GET  /api/health"),
        ]),
        ("AGENT RESET  ( /api/reset )", [
            ("/agent reset",               "Reset server-side agent      POST /api/reset"),
        ]),
        ("EVOLUTION  ( /api/evolution/* )", [
            ("/evo start [skill] [desc]",  "Start evolution   POST /api/evolution/start"),
            ("/evo status [ws_id]",        "Get status        GET  /api/evolution/status"),
            ("/evo approve [ws_id]",       "Approve           POST /api/evolution/approve"),
            ("/evo revise [ws_id]",        "Request revision  POST /api/evolution/revise"),
            ("/evo reset [ws_id]",         "Reset             POST /api/evolution/reset"),
            ("/evo logs [ws_id]",          "Fetch logs        GET  /api/evolution/logs"),
        ]),
        ("RAW API", [
            ("/api <METHOD> <path> [json]","Raw call  e.g.  /api GET /api/health"),
            ("                           ","         e.g.  /api POST /api/evolution/approve {workspace_id: value}"),
        ]),
        ("SESSION", [
            ("exit / quit",               "Exit the console"),
        ]),
    ]
    print()
    for section, cmds in sections:
        print(section_bar(section))
        for cmd, desc in cmds:
            if cmd.startswith("  ") or not desc:
                print(f"  {paint(cmd, DIM, GRAY)}")
            else:
                print(f"  {paint(cmd.ljust(36), BYELLOW)}  {paint(desc, DIM, WHITE)}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
#  HTTP HELPER  (stdlib only — no requests dependency)
# ─────────────────────────────────────────────────────────────────────────────
def api_call(api_url: str, method: str, path: str, body: dict = None) -> dict:
    url  = api_url.rstrip("/") + path
    data = json.dumps(body).encode() if body else None
    hdrs = {"Content-Type": "application/json", "Accept": "application/json"}
    req  = urllib.request.Request(url, data=data, headers=hdrs, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return {"__error__": json.loads(raw)}
        except Exception:
            return {"__error__": raw or str(e)}
    except Exception as ex:
        return {"__error__": str(ex)}


def print_api_result(result: dict, label: str = ""):
    if label:
        print(f"\n  {paint(label, BOLD, BCYAN)}")
    if "__error__" in result:
        print(f"  {paint('✗', BRED)}  {paint(str(result['__error__']), RED)}")
    else:
        for line in json.dumps(result, indent=2).splitlines():
            print(f"  {paint(line, DIM, WHITE)}")


# ─────────────────────────────────────────────────────────────────────────────
#  API COMMAND HANDLERS
# ─────────────────────────────────────────────────────────────────────────────

def cmd_health(api_url: str):
    print(f"\n  {paint('▸', CYAN)} {method_badge('GET')} {paint('/api/health', GRAY)}")
    result = api_call(api_url, "GET", "/api/health")
    if "__error__" not in result:
        status = result.get("status", "?")
        dot    = paint("●", BGREEN) if status == "ok" else paint("●", BRED)
        print(f"\n  {dot}  {paint('Status:', GRAY)} {paint(status.upper(), BOLD, BGREEN)}")
        print(f"  {paint('Provider:', GRAY)} {paint(result.get('provider','?') + '/' + result.get('model','?'), WHITE)}")
        print(f"  {paint('Skills:', GRAY)}   {paint(str(result.get('skills','?')), BCYAN)}")
    else:
        print_api_result(result)
    print()


def cmd_config_get(api_url: str):
    print(f"\n  {paint('▸', CYAN)} {method_badge('GET')} {paint('/api/config', GRAY)}")
    result = api_call(api_url, "GET", "/api/config")
    if "__error__" not in result:
        rows = [
            ("Provider",       result.get("provider", "?")),
            ("Model",          result.get("model", "?")),
            ("Key configured", str(result.get("key_configured", False))),
            ("Providers",      ", ".join(result.get("available_providers", []))),
        ]
        print()
        for k, v in rows:
            print(f"  {paint(k.ljust(18), GRAY)}  {paint(v, WHITE)}")
    else:
        print_api_result(result)
    print()


def cmd_config_set(api_url: str, args: list):
    payload = {}
    for token in args:
        if "=" in token:
            k, _, v = token.partition("=")
            payload[k.strip()] = v.strip()
    if not payload:
        print(f"\n  {paint('✗', BRED)}  Usage: /config set provider=deepseek model=deepseek-chat\n")
        return
    print(f"\n  {paint('▸', CYAN)} {method_badge('POST')} {paint('/api/config', GRAY)}")
    print(f"  {paint('Payload:', DIM, GRAY)} {paint(json.dumps(payload), DIM, WHITE)}")
    result = api_call(api_url, "POST", "/api/config", payload)
    if "__error__" not in result:
        print(f"\n  {paint('✓', BGREEN)}  Config updated — "
              f"{paint(result.get('provider','?') + '/' + result.get('model','?'), WHITE)}")
    else:
        print_api_result(result)
    print()


def cmd_skills_api(api_url: str):
    print(f"\n  {paint('▸', CYAN)} {method_badge('GET')} {paint('/api/skills', GRAY)}")
    result = api_call(api_url, "GET", "/api/skills")
    if "__error__" not in result:
        skills = result.get("skills", [])
        print(f"\n  {paint('SKILLS', BOLD, BCYAN)}  {paint(f'({len(skills)})', DIM, GRAY)}")
        print(paint("  " + "─" * 44, GRAY))
        for i, s in enumerate(skills, 1):
            name = s.get("name", s) if isinstance(s, dict) else str(s)
            desc = (s.get("description", "") if isinstance(s, dict) else "")
            print(f"  {paint(str(i).rjust(3), DIM, GRAY)}  {paint('▸', CYAN)} "
                  f"{paint(name, WHITE)}"
                  + (f"  {paint(desc, DIM, GRAY)}" if desc else ""))
    else:
        print_api_result(result)
    print()


def cmd_agent_reset(api_url: str):
    print(f"\n  {paint('▸', CYAN)} {method_badge('POST')} {paint('/api/reset', GRAY)}")
    result = api_call(api_url, "POST", "/api/reset", {})
    if "__error__" not in result:
        print(f"\n  {paint('✓', BGREEN)}  Server-side agent reset.")
    else:
        print_api_result(result)
    print()


def cmd_evo(api_url: str, args: list):
    sub  = args[0].lower() if args else "status"
    rest = args[1:]

    if sub == "start":
        target = rest[0] if len(rest) > 0 else ""
        desc   = " ".join(rest[1:]) if len(rest) > 1 else ""
        body   = {"target_skill": target, "task_description": desc}
        print(f"\n  {paint('▸', CYAN)} {method_badge('POST')} {paint('/api/evolution/start', GRAY)}")
        print(f"  {paint('target:', DIM, GRAY)} {paint(target or '(none)', WHITE)}"
              f"  {paint('desc:', DIM, GRAY)} {paint(desc or '(none)', WHITE)}")
        result = api_call(api_url, "POST", "/api/evolution/start", body)
        if "__error__" not in result:
            ws_id = result.get("workspace_id", "?")
            state = result.get("state", {})
            print(f"\n  {paint('✓', BGREEN)}  Evolution started")
            print(f"  {paint('Workspace ID:', GRAY)} {paint(ws_id, BCYAN, BOLD)}")
            if state:
                print(f"  {paint('Phase:', GRAY)} {paint(str(state.get('current_phase','?')), WHITE)}")
        else:
            print_api_result(result)

    elif sub == "status":
        ws_id = rest[0] if rest else None
        path  = "/api/evolution/status" + (f"?workspace_id={ws_id}" if ws_id else "")
        print(f"\n  {paint('▸', CYAN)} {method_badge('GET')} {paint(path, GRAY)}")
        result = api_call(api_url, "GET", path)
        if "__error__" not in result:
            state = result.get("state", {})
            print(f"\n  {paint('Status:', GRAY)}  {paint(str(state.get('status','?')), BOLD, BYELLOW)}")
            print(f"  {paint('Phase:', GRAY)}   {paint(str(state.get('current_phase','?')), WHITE)}")
            print(f"  {paint('Target:', GRAY)}  {paint(str(state.get('target_skill','?')), WHITE)}")
        else:
            print_api_result(result)

    elif sub == "approve":
        ws_id  = rest[0] if rest else None
        body   = {"workspace_id": ws_id}
        print(f"\n  {paint('▸', CYAN)} {method_badge('POST')} {paint('/api/evolution/approve', GRAY)}")
        result = api_call(api_url, "POST", "/api/evolution/approve", body)
        if "__error__" not in result:
            print(f"\n  {paint('✓', BGREEN)}  Evolution approved.")
        else:
            print_api_result(result)

    elif sub == "revise":
        ws_id  = rest[0] if rest else None
        body   = {"workspace_id": ws_id}
        print(f"\n  {paint('▸', CYAN)} {method_badge('POST')} {paint('/api/evolution/revise', GRAY)}")
        result = api_call(api_url, "POST", "/api/evolution/revise", body)
        if "__error__" not in result:
            print(f"\n  {paint('✓', BGREEN)}  Revision requested.")
        else:
            print_api_result(result)

    elif sub == "reset":
        ws_id  = rest[0] if rest else None
        body   = {"workspace_id": ws_id}
        print(f"\n  {paint('▸', CYAN)} {method_badge('POST')} {paint('/api/evolution/reset', GRAY)}")
        result = api_call(api_url, "POST", "/api/evolution/reset", body)
        if "__error__" not in result:
            print(f"\n  {paint('✓', BGREEN)}  Evolution reset.")
        else:
            print_api_result(result)

    elif sub == "logs":
        ws_id = rest[0] if rest else None
        path  = "/api/evolution/logs" + (f"?workspace_id={ws_id}" if ws_id else "")
        print(f"\n  {paint('▸', CYAN)} {method_badge('GET')} {paint(path, GRAY)}")
        result = api_call(api_url, "GET", path)
        if "__error__" not in result:
            logs = result.get("logs", [])
            print(f"\n  {paint('EVOLUTION LOGS', BOLD, BCYAN)}  {paint(f'({len(logs)} entries)', DIM, GRAY)}")
            print(paint("  " + "─" * 50, GRAY))
            for entry in logs:
                ts  = entry.get("timestamp", "")       if isinstance(entry, dict) else ""
                msg = entry.get("message", str(entry)) if isinstance(entry, dict) else str(entry)
                lvl = entry.get("level", "INFO")        if isinstance(entry, dict) else "INFO"
                lc  = BGREEN if lvl == "INFO" else (BYELLOW if lvl == "WARNING" else BRED)
                print(f"  {paint(ts, DIM, GRAY)}  {paint(lvl.ljust(7), lc)}  {paint(msg, WHITE)}")
        else:
            print_api_result(result)

    else:
        print(f"\n  {paint('✗', BRED)}  Unknown sub-command: {paint(sub, BYELLOW)}")
        print(f"  Use: {paint('start | status | approve | revise | reset | logs', GRAY)}")

    print()


def cmd_raw_api(api_url: str, args: list):
    if len(args) < 2:
        print(f"\n  {paint('✗', BRED)}  Usage: /api <METHOD> <path> [json]\n")
        return
    method = args[0].upper()
    path   = args[1]
    body   = None
    if len(args) >= 3:
        try:
            body = json.loads(" ".join(args[2:]))
        except json.JSONDecodeError as e:
            print(f"\n  {paint('✗', BRED)}  JSON parse error: {e}\n")
            return
    print(f"\n  {paint('▸', CYAN)} {method_badge(method)} {paint(path, GRAY)}")
    print_api_result(api_call(api_url, method, path, body))
    print()


# ─────────────────────────────────────────────────────────────────────────────
#  AGENT INIT
# ─────────────────────────────────────────────────────────────────────────────
def init_agent():
    reg = SkillRegistry()
    reg.register("filesystem",    FileSystemSkill())
    reg.register("os_execution",  OSExecutionSkill())
    reg.register("cbd_architect", CBDArchitectSkill())
    try:
        from skills.self_evolution_skill.evolution_skill import SelfEvolutionSkill
        reg.register("self_evolution", SelfEvolutionSkill())
    except ImportError:
        pass
    try:
        from skills import load_all_skills
        load_all_skills(reg)
    except Exception:
        pass
    p   = os.getenv("LLM_PROVIDER", "deepseek")
    d   = PROVIDER_DEFAULTS.get(p, PROVIDER_DEFAULTS["deepseek"])
    k   = (os.getenv(d.get("key_env", ""), "")
           or os.getenv("DEEPSEEK_API_KEY", "")
           or os.getenv("OPENAI_API_KEY", ""))
    cfg = LLMConfig(
        provider      = p,
        model         = os.getenv("LLM_MODEL", d.get("model", "deepseek-chat")),
        api_key       = k,
        base_url      = os.getenv("LLM_BASE_URL", d.get("base_url", "")),
        temperature   = float(os.getenv("LLM_TEMPERATURE", "0.7")),
        max_tokens    = int(os.getenv("LLM_MAX_TOKENS", "4096")),
        schema_format = os.getenv("LLM_SCHEMA_FORMAT") or None,
    )
    return Agent(cfg, reg), p, cfg.model


# ─────────────────────────────────────────────────────────────────────────────
#  STREAM EVENT RENDERER
# ─────────────────────────────────────────────────────────────────────────────
async def handle_stream(stream, agent):
    response_started = False

    try:
        async for ev in stream:
            t = ev.get("type", "")
            d = ev.get("data", {})

            if t == "token":
                if not response_started:
                    print(f"\n  {paint('◆', BCYAN)}  ", end="", flush=True)
                    response_started = True
                tk = d if isinstance(d, str) else d.get("content", d.get("data", ""))
                if tk:
                    print(tk, end="", flush=True)

            elif t == "tool_call":
                if response_started:
                    print()
                skill  = d.get("skill", "?")
                action = d.get("action", "?")
                print(f"\n  {paint('⚙', BYELLOW)}  {paint('TOOL', BOLD, YELLOW)} "
                      f"{paint(skill, BCYAN)}{paint('.', GRAY)}{paint(action, CYAN)} "
                      f"{paint('…', DIM, GRAY)}")
                response_started = False

            elif t == "tool_result":
                if d.get("success"):
                    print(f"  {paint('✓', BGREEN)}  {paint('Done', DIM, GRAY)}")
                else:
                    err = d.get("error", "Unknown error")
                    print(f"  {paint('✗', BRED)}  {paint(f'Error: {err}', RED)}")

            elif t == "confirm_needed":
                print()
                prompt = d.get("prompt", "Proceed with this action?")
                print(f"\n  {paint('⚠', BYELLOW, BOLD)}  {paint('CONFIRMATION REQUIRED', BOLD, YELLOW)}")
                print(f"  {paint('│', GRAY)}  {paint(prompt, WHITE)}")
                print(f"  {paint('│', GRAY)}")
                answer = input(f"  {paint('└─', GRAY)}  {paint('Confirm?', BOLD, YELLOW)} {paint('(y/n):', GRAY)} ")
                if answer.strip().lower().startswith("y"):
                    print(f"  {paint('✓', BGREEN)}  {paint('Confirmed — executing…', DIM, GRAY)}\n")
                    async for ce in agent.confirm_action(d.get("confirm_id", "")):
                        ct = ce.get("type", "")
                        cd = ce.get("data", {})
                        if ct == "token":
                            if not response_started:
                                print(f"\n  {paint('◆', BCYAN)}  ", end="", flush=True)
                                response_started = True
                            tk2 = cd if isinstance(cd, str) else cd.get("content", "")
                            if tk2:
                                print(tk2, end="", flush=True)
                        elif ct == "tool_result":
                            if cd.get("success"):
                                print(f"\n  {paint('✓', BGREEN)}  {paint('Done', DIM, GRAY)}")
                            else:
                                confirm_err = cd.get("error", "Unknown error")
                                print(f"\n  {paint('✗', BRED)}  {paint(f'Error: {confirm_err}', RED)}")
                else:
                    print(f"  {paint('✗', GRAY)}  {paint('Action cancelled.', DIM, GRAY)}")
                    return

            elif t == "react_status" and d.get("healing"):
                iteration = d.get("iteration", "?")
                phase     = d.get("phase", "?")
                print(f"\n  {paint('↻', BMAGENTA)}  {paint('REACT', BOLD, MAGENTA)} "
                      f"{paint(f'iter {iteration}', DIM, GRAY)} {paint('·', GRAY)} "
                      f"{paint(phase, ITALIC, GRAY)}")
                response_started = False

            elif t == "error":
                print(f"\n  {paint('✗', BRED)}  {paint(str(d), RED)}")

    except Exception as stream_err:
        if response_started:
            print()
        print(f"\n  {paint('✗', BRED)}  {paint(f'Stream error: {stream_err}', RED)}")

    if response_started:
        print()


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN REPL
# ─────────────────────────────────────────────────────────────────────────────
async def repl(agent, react: bool, auto_confirm: bool,
               provider: str, model: str, api_url: str):
    skill_count = len(agent.registry.list_skills())
    turn = 0

    print_banner()
    print_status_bar(react, auto_confirm, skill_count, provider, model, api_url)
    print()
    print(f"  {paint('Type', DIM, GRAY)} {paint('/help', BCYAN)} "
          f"{paint('for all commands  ·  API target:', DIM, GRAY)} {paint(api_url, BLUE)}")
    print()

    while True:
        try:
            turn += 1
            prompt_prefix = (f"{paint(f'[{turn:03}]', DIM, GRAY)} "
                             f"{paint('Jarvis', BOLD, BCYAN)} "
                             f"{paint('›', CYAN)} ")
            user_input = input(prompt_prefix)

            if not user_input.strip():
                turn -= 1
                continue

            raw_cmd = user_input.strip()
            cmd     = raw_cmd.lower()
            tokens  = raw_cmd.split()

            # ── Exit ─────────────────────────────────────────
            if cmd in ("exit", "quit", "q", "/exit", "/quit"):
                print()
                print(f"  {paint('◉', CYAN)}  {paint('Session ended.', DIM, WHITE)}  "
                      f"{paint(str(turn - 1) + ' turns', DIM, GRAY)}")
                print()
                break


            # ── Agent ─────────────────────────────────────────
            if cmd in ("/reset", "reset"):
                agent.reset(); turn = 0
                print(f"\n  {paint('↺', BYELLOW)}  {paint('Conversation history cleared.', DIM, GRAY)}\n")
                continue

            if cmd == "/react":
                react = not react
                state = paint("ON", BOLD, BGREEN) if react else paint("OFF", BOLD, GRAY)
                print(f"\n  {paint('◈', BCYAN)}  {paint('ReAct mode:', GRAY)} {state}\n")
                continue

            if cmd == "/status":
                print()
                print_status_bar(react, auto_confirm, skill_count, provider, model, api_url)
                print()
                continue

            if cmd == "/clear":
                os.system("cls" if os.name == "nt" else "clear")
                print_banner()
                print_status_bar(react, auto_confirm, skill_count, provider, model, api_url)
                print()
                turn -= 1
                continue

            if cmd in ("/help", "help", "/?"):
                print_help()
                turn -= 1
                continue

            if cmd == "/skills":
                raw_skills = agent.registry.list_skills()
                skill_names = sorted(
                    (s.get("name", str(s)) if isinstance(s, dict) else str(s))
                    for s in raw_skills
                )
                print()
                print(f"  {paint('LOCAL SKILLS', BOLD, BCYAN)}  {paint(f'({len(skill_names)})', DIM, GRAY)}")
                print(paint("  " + "─" * 40, GRAY))
                for i, name in enumerate(skill_names, 1):
                    print(f"  {paint(str(i).rjust(3), DIM, GRAY)}  {paint('▸', CYAN)} {paint(name, WHITE)}")
                print()
                continue

            # ── API — Health ──────────────────────────────────
            if cmd == "/health":
                cmd_health(api_url); turn -= 1; continue

            # ── API — Config ──────────────────────────────────
            if cmd == "/config":
                cmd_config_get(api_url); turn -= 1; continue

            if cmd.startswith("/config set"):
                cmd_config_set(api_url, tokens[2:]); turn -= 1; continue

            # ── API — Skills ──────────────────────────────────
            if cmd == "/skills list":
                cmd_skills_api(api_url); turn -= 1; continue

            # ── API — Agent reset ─────────────────────────────
            if cmd in ("/agent reset", "/api reset"):
                cmd_agent_reset(api_url); turn -= 1; continue

            # ── API — Evolution ───────────────────────────────
            if cmd.startswith("/evo"):
                cmd_evo(api_url, tokens[1:]); turn -= 1; continue

            # ── API — Raw call ────────────────────────────────
            if cmd.startswith("/api "):
                cmd_raw_api(api_url, tokens[1:]); turn -= 1; continue

            # ── Agent chat ────────────────────────────────────
            print(divider())
            try:
                stream = agent.chat_stream(user_input, react=react, auto_confirm=auto_confirm)
                await handle_stream(stream, agent)
            except Exception as e:
                print(f"\n  {paint('✗', BRED)}  {paint(f'Stream error: {e}', RED)}\n")
            print(divider())
            print()

        except KeyboardInterrupt:
            print()
            print(f"\n  {paint('◉', CYAN)}  {paint('Interrupted. Goodbye.', DIM, WHITE)}\n")
            break
        except EOFError:
            print()
            print(f"\n  {paint('◉', CYAN)}  {paint('EOF. Goodbye.', DIM, WHITE)}\n")
            break


# ─────────────────────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
def main():
    react        = "--no-react"     not in sys.argv
    auto_confirm = "--auto-confirm" in sys.argv
    api_url      = os.getenv("JARVIS_API_URL", "http://localhost:3000/")
    for arg in sys.argv[1:]:
        if arg.startswith("--api="):
            api_url = arg.split("=", 1)[1]

    print(paint("  Initializing Jarvis…", DIM, GRAY))
    try:
        agent, provider, model = init_agent()
    except Exception as e:
        print(paint(f"  ✗  Initialization failed: {e}", BRED, BOLD))
        sys.exit(1)

    skill_count = len(agent.registry.list_skills())
    print(paint(f"  ✓  {skill_count} skills loaded  ·  {provider}/{model}", DIM, BGREEN))
    print(paint(f"  ◉  API target: {api_url}", DIM, BLUE))

    try:
        asyncio.run(repl(agent, react=react, auto_confirm=auto_confirm,
                         provider=provider, model=model, api_url=api_url))
    except KeyboardInterrupt:
        print()
        print(paint("  Goodbye.", DIM, WHITE))
        print()


if __name__ == "__main__":
    main()