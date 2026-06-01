"""
SKILL: OSExecution
CBD Identity:
  Name: OSExecutionSkill
  Reason: Give the agent full OS control — run processes, shell commands, manage services.
  Logical Function: Execution, System Control

IN-Schema:  { action, command?, pid?, signal?, env?, cwd?, timeout? }
OUT-Schema: { success, output: { stdout, stderr, returncode, pid } }
Error-Schema: { success: false, error: "<message>" }

Safety Model: CONFIRM before every exec/kill/signal action.
"""
import asyncio
import os
import signal
import psutil
import logging
from core.skill_registry import SkillResult

logger = logging.getLogger("skill.os_execution")

DESTRUCTIVE_ACTIONS = {"run_command", "kill_process", "send_signal"}


class OSExecutionSkill:
    description = "Full OS control: run shell commands, manage processes, inspect system resources."
    actions = [
        "run_command", "list_processes", "kill_process",
        "send_signal", "system_info", "env_vars",
    ]

    async def execute(self, action: str, params: dict, confirmed: bool = False) -> SkillResult:
        # Safety gate
        if action in DESTRUCTIVE_ACTIONS and not confirmed:
            cmd = params.get("command") or params.get("pid") or "?"
            return SkillResult(
                success=False,
                output=None,
                requires_confirm=True,
                confirm_prompt=f"⚠️ OS action `{action}` → `{cmd}`. Confirm to execute on host.",
            )

        try:
            if action == "run_command":
                return await self._run_command(params)
            elif action == "list_processes":
                return self._list_processes(params)
            elif action == "kill_process":
                return self._kill_process(params)
            elif action == "send_signal":
                return self._send_signal(params)
            elif action == "system_info":
                return self._system_info()
            elif action == "env_vars":
                return self._env_vars(params)
            else:
                return SkillResult(False, None, error=f"Unknown action: {action}")
        except Exception as e:
            logger.error(f"OSExecutionSkill.{action} error: {e}", exc_info=True)
            return SkillResult(False, None, error=f"{type(e).__name__}: {str(e)}")

    async def _run_command(self, params: dict) -> SkillResult:
        command = params.get("command")
        if not command:
            return SkillResult(False, None, error="'command' is required.")
        cwd = params.get("cwd", os.getcwd())
        timeout = params.get("timeout", 60)
        env = {**os.environ, **(params.get("env") or {})}

        logger.warning(f"Executing command: {command} (cwd={cwd})")
        proc = None
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return SkillResult(True, {
                "command": command,
                "stdout": stdout.decode(errors="replace"),
                "stderr": stderr.decode(errors="replace"),
                "returncode": proc.returncode,
                "pid": proc.pid,
            })
        except asyncio.TimeoutError:
            if proc is not None:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
            return SkillResult(False, None, error=f"Command timed out after {timeout}s")

    def _list_processes(self, params: dict) -> SkillResult:
        filter_name = params.get("name_filter", "").lower()
        procs = []
        for p in psutil.process_iter(["pid", "name", "status", "cpu_percent", "memory_info", "cmdline"]):
            try:
                info = p.info
                if filter_name and filter_name not in (info.get("name") or "").lower():
                    continue
                procs.append({
                    "pid": info["pid"],
                    "name": info["name"],
                    "status": info["status"],
                    "cpu_percent": info["cpu_percent"],
                    "memory_mb": round((info["memory_info"].rss / 1024 / 1024), 2) if info["memory_info"] else None,
                    "cmdline": " ".join(info.get("cmdline") or [])[:120],
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return SkillResult(True, {"processes": procs, "count": len(procs)})

    def _kill_process(self, params: dict) -> SkillResult:
        pid = params.get("pid")
        if not pid:
            return SkillResult(False, None, error="'pid' is required.")
        try:
            proc = psutil.Process(int(pid))
            proc.kill()
            logger.warning(f"Killed PID {pid}")
            return SkillResult(True, {"killed_pid": pid})
        except psutil.NoSuchProcess:
            return SkillResult(False, None, error=f"Process {pid} not found.")

    def _send_signal(self, params: dict) -> SkillResult:
        pid = params.get("pid")
        sig = params.get("signal", "SIGTERM")
        if not pid:
            return SkillResult(False, None, error="'pid' is required.")
        sig_num = getattr(signal, sig, None)
        if sig_num is None:
            return SkillResult(False, None, error=f"Unknown signal name: '{sig}'. Use e.g. SIGTERM, SIGKILL, SIGHUP.")
        os.kill(int(pid), sig_num)
        logger.warning(f"Sent {sig} to PID {pid}")
        return SkillResult(True, {"pid": pid, "signal": sig})

    def _system_info(self) -> SkillResult:
        import platform
        vm = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        return SkillResult(True, {
            "platform": platform.system(),
            "platform_release": platform.release(),
            "architecture": platform.machine(),
            "cpu_count": psutil.cpu_count(),
            "cpu_percent": psutil.cpu_percent(interval=0.5),
            "memory_total_gb": round(vm.total / 1e9, 2),
            "memory_used_gb": round(vm.used / 1e9, 2),
            "memory_percent": vm.percent,
            "disk_total_gb": round(disk.total / 1e9, 2),
            "disk_free_gb": round(disk.free / 1e9, 2),
            "disk_percent": disk.percent,
        })

    def _env_vars(self, params: dict) -> SkillResult:
        key = params.get("key")
        if key:
            return SkillResult(True, {"key": key, "value": os.environ.get(key)})
        # Redact sensitive keys when dumping full env to prevent leaking API keys/secrets
        _sensitive = {"KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD", "CREDENTIAL", "AUTH"}
        redacted = {
            k: ("***REDACTED***" if any(s in k.upper() for s in _sensitive) else v)
            for k, v in os.environ.items()
        }
        return SkillResult(True, {"env": redacted, "note": "Sensitive keys redacted. Use key= param to read a specific value."})
