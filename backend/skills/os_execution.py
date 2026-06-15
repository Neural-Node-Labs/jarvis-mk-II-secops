"""
OS Execution Skill — Full OS control via async subprocess.
Kali Linux native. Optimised for security tool execution.

version: 1.1.0
changelog:
  1.1.0 - 2026-06-14 - Production hardening.
    FIXED  _run_command: shell=False branch used `command.split()` which breaks
           any command containing quoted arguments or paths with spaces.
           shell=False now requires params["argv"] (list) instead; shell=True
           remains the default and is still used when argv is absent.
    FIXED  _run_command: after asyncio.TimeoutError, proc.kill() was called but
           not awaited; the zombie process was left running.  Now uses
           proc.kill() + await proc.wait() to fully reap it.
    FIXED  _is_destructive: the pattern "kill" matched any command containing
           the substring "kill" (e.g. "skill", "toolkit").  Patterns now require
           a word boundary (space or start-of-string) and are checked more
           precisely.
    FIXED  _env_vars: redaction checked `k.upper()` against uppercase strings
           but the check was case-sensitive on the value side; some mixed-case
           key names slipped through.  Now normalises both sides.
    FIXED  _kill_process: `pkill -TERM -f {name}` passes unsanitised user input
           directly to shell — a name containing shell metacharacters executes
           arbitrary commands.  Now uses shlex.quote.
  1.0.0 - 2026-06-10 - Initial implementation.
"""
import asyncio
import os
import shlex
import signal
import logging
import traceback
import platform
from datetime import datetime, timezone
from core.skill_registry import SkillResult

logger = logging.getLogger("skill.os_execution")

OUTPUT_CAP   = 128_000   # bytes — hard cap on stdout+stderr returned to agent
DEFAULT_TIMEOUT = 120    # seconds

# These command prefixes always require confirmation
DESTRUCTIVE_PREFIXES = (
    "rm ", "rm\t", "rmdir", "dd ", "mkfs", "fdisk", "wipefs",
    "shred", "chmod 777", "> /", "truncate",
    "systemctl stop", "systemctl disable",
    "iptables -F", "ufw disable",
    "kill", "killall", "pkill",
    "DROP TABLE", "DROP DATABASE",
)

_KALI_ENV = {
    **os.environ,
    "PATH":            "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "TERM":            "xterm-256color",
    "DEBIAN_FRONTEND": "noninteractive",
}


class OsExecutionSkill:

    async def execute(self, action: str, params: dict, confirmed: bool = False) -> SkillResult:
        try:
            dispatch = {
                "run_command":    self._run_command,
                "list_processes": self._list_processes,
                "kill_process":   self._kill_process,
                "send_signal":    self._send_signal,
                "system_info":    self._system_info,
                "env_vars":       self._env_vars,
            }
            fn = dispatch.get(action)
            if fn is None:
                return SkillResult.fail(f"OS_UNKNOWN_ACTION: '{action}'. Valid: {', '.join(dispatch)}")
            return await fn(params, confirmed)
        except Exception as exc:
            logger.error("[os_exec.%s] %s\n%s", action, exc, traceback.format_exc())
            return SkillResult.fail(f"OS_INTERNAL_ERROR: {type(exc).__name__}: {exc}")

    # ── run_command ────────────────────────────────────────────────────────────

    async def _run_command(self, params: dict, confirmed: bool) -> SkillResult:
        command = params.get("command", "").strip()
        cwd     = params.get("cwd", "/tmp")
        timeout = int(params.get("timeout", DEFAULT_TIMEOUT))
        env_ext = params.get("env", {})
        shell   = params.get("shell", True)

        if not command:
            return SkillResult.fail("OS_MISSING_PARAM: command is required")

        # Confirmation gate for destructive commands
        if not confirmed and _is_destructive(command):
            return SkillResult.confirm(
                f"⚠️ Destructive command detected:\n```\n{command}\n```\nConfirm execution?",
                output={"command": command, "cwd": cwd},
            )

        env = {**_KALI_ENV, **env_ext}
        started = datetime.now(timezone.utc)
        logger.info("[os.run_command] cmd=%.100s cwd=%s timeout=%d", command, cwd, timeout)

        try:
            # FIX: shell=False requires an explicit argv list via params["argv"]
            # so that paths/arguments with spaces are not mangled by str.split().
            argv = params.get("argv")
            if shell or argv is None:
                proc = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                    env=env,
                )
            else:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                    env=env,
                )

            try:
                stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                # FIX: kill() + wait() to fully reap the zombie; without wait()
                # the process keeps running and its stdout pipe stays open.
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
                duration = timeout * 1000
                logger.warning("[os.timeout] cmd=%.80s after=%ds", command, timeout)
                return SkillResult.ok({
                    "command":     command,
                    "stdout":      "",
                    "stderr":      f"[TIMEOUT] Command killed after {timeout}s",
                    "returncode":  -1,
                    "timed_out":   True,
                    "duration_ms": duration,
                })

            duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            stdout = stdout_b.decode("utf-8", errors="replace")
            stderr = stderr_b.decode("utf-8", errors="replace")

            # Cap output
            if len(stdout) > OUTPUT_CAP:
                stdout = stdout[:OUTPUT_CAP] + f"\n… [truncated — {len(stdout_b)} bytes total]"
            if len(stderr) > OUTPUT_CAP // 4:
                stderr = stderr[:OUTPUT_CAP // 4] + f"\n… [stderr truncated]"

            logger.info("[os.run_complete] rc=%d duration_ms=%d", proc.returncode, duration_ms)
            return SkillResult.ok({
                "command":     command,
                "stdout":      stdout,
                "stderr":      stderr,
                "returncode":  proc.returncode,
                "timed_out":   False,
                "duration_ms": duration_ms,
                "cwd":         cwd,
            })

        except FileNotFoundError:
            return SkillResult.fail(f"OS_NOT_FOUND: working directory '{cwd}' does not exist")
        except PermissionError as exc:
            return SkillResult.fail(f"OS_PERMISSION: {exc}")
        except Exception as exc:
            return SkillResult.fail(f"OS_RUN_ERROR: {exc}")

    # ── list_processes ─────────────────────────────────────────────────────────

    async def _list_processes(self, params: dict, confirmed: bool) -> SkillResult:
        filter_name = params.get("name", "")
        try:
            cmd = "ps aux --no-headers"
            if filter_name:
                cmd += f" | grep -i {filter_name} | grep -v grep"
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env=_KALI_ENV,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            lines = out.decode("utf-8", errors="replace").strip().splitlines()
            processes = []
            for line in lines[:200]:
                parts = line.split(None, 10)
                if len(parts) >= 11:
                    processes.append({
                        "user":    parts[0],
                        "pid":     parts[1],
                        "cpu":     parts[2],
                        "mem":     parts[3],
                        "command": parts[10],
                    })
            return SkillResult.ok({"processes": processes, "count": len(processes)})
        except Exception as exc:
            return SkillResult.fail(f"OS_PS_ERROR: {exc}")

    # ── kill_process ───────────────────────────────────────────────────────────

    async def _kill_process(self, params: dict, confirmed: bool) -> SkillResult:
        pid  = params.get("pid")
        name = params.get("name", "")
        if pid is None and not name:
            return SkillResult.fail("OS_MISSING_PARAM: pid or name is required")

        if not confirmed:
            target = f"PID {pid}" if pid else f"process '{name}'"
            return SkillResult.confirm(f"⚠️ About to KILL {target}. Confirm?")

        try:
            if pid:
                os.kill(int(pid), signal.SIGTERM)
                return SkillResult.ok({"killed": True, "pid": pid, "signal": "SIGTERM"})
            else:
                # FIX: quote name to prevent shell injection via metacharacters
                safe_name = shlex.quote(name)
                result = await self._run_command(
                    {"command": f"pkill -TERM -f {safe_name}"},
                    confirmed=True,
                )
                return result
        except ProcessLookupError:
            return SkillResult.fail(f"OS_NO_PROCESS: PID {pid} not found")
        except Exception as exc:
            return SkillResult.fail(f"OS_KILL_ERROR: {exc}")

    # ── send_signal ────────────────────────────────────────────────────────────

    async def _send_signal(self, params: dict, confirmed: bool) -> SkillResult:
        pid     = params.get("pid")
        sig_num = int(params.get("signal", signal.SIGUSR1))
        if pid is None:
            return SkillResult.fail("OS_MISSING_PARAM: pid is required")
        try:
            os.kill(int(pid), sig_num)
            return SkillResult.ok({"sent": True, "pid": pid, "signal": sig_num})
        except Exception as exc:
            return SkillResult.fail(f"OS_SIGNAL_ERROR: {exc}")

    # ── system_info ────────────────────────────────────────────────────────────

    async def _system_info(self, params: dict, confirmed: bool) -> SkillResult:
        try:
            cmds = {
                "hostname": "hostname",
                "kernel":   "uname -r",
                "arch":     "uname -m",
                "os":       "cat /etc/os-release | head -5",
                "uptime":   "uptime -p",
                "memory":   "free -h | head -2",
                "disk":     "df -h / | tail -1",
                "cpu":      "nproc && cat /proc/cpuinfo | grep 'model name' | head -1",
                "ip":       "ip -brief addr show 2>/dev/null || ifconfig 2>/dev/null | head -10",
            }
            info = {}
            for key, cmd in cmds.items():
                proc = await asyncio.create_subprocess_shell(
                    cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    env=_KALI_ENV,
                )
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
                info[key] = out.decode("utf-8", errors="replace").strip()

            return SkillResult.ok(info)
        except Exception as exc:
            return SkillResult.fail(f"OS_SYSINFO_ERROR: {exc}")

    # ── env_vars ───────────────────────────────────────────────────────────────

    async def _env_vars(self, params: dict, confirmed: bool) -> SkillResult:
        """Return current environment variables. Redacts known sensitive keys."""
        REDACT = {"API_KEY", "SECRET", "PASSWORD", "TOKEN", "PASS", "PRIVATE", "KEY", "CREDENTIAL"}
        env = {}
        for k, v in os.environ.items():
            # FIX: normalise k to uppercase before substring check so mixed-case
            # names like "Api_Key" or "db_password" are also redacted.
            k_upper = k.upper()
            if any(r in k_upper for r in REDACT):
                env[k] = "[REDACTED]"
            else:
                env[k] = v
        return SkillResult.ok({"env": env, "count": len(env)})


# ── Helpers ────────────────────────────────────────────────────────────────────

def _is_destructive(command: str) -> bool:
    """
    FIX: original used `startswith(p)` and `f' {p}' in cmd_lower` which caused
    false positives — 'kill' matched 'skill', 'toolkit', etc.
    Now checks: command starts with the pattern OR the pattern is preceded by a
    space/semicolon/pipe (i.e. a real word boundary in shell).
    """
    cmd_lower = command.lower().strip()
    for p in DESTRUCTIVE_PREFIXES:
        p_l = p.lower().rstrip()   # strip training space from patterns like "rm "
        if cmd_lower == p_l:
            return True
        if cmd_lower.startswith(p_l + " ") or cmd_lower.startswith(p_l + "\t"):
            return True
        # Also catch chained commands:  && kill ..., ; rm ..., | dd ...
        for sep in (" && ", "; ", " | ", "\n"):
            for part in cmd_lower.split(sep):
                part = part.strip()
                if part == p_l or part.startswith(p_l + " ") or part.startswith(p_l + "\t"):
                    return True
    return False