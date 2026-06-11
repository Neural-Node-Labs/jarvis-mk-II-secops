"""
Sentinel Skill — Autonomous security monitoring and threat response.
Adapted from Sentinel-AI. No LangGraph dependency. Registered as a first-class Jarvis skill.

version: 1.0.0
changelog:
  1.0.0 - Initial implementation. Actions: start_monitoring, stop_monitoring, status,
          analyze_logs, block_ip. Background asyncio monitoring loop.
"""
import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.skill_registry import SkillResult

logger = logging.getLogger("skill.sentinel")
DEFAULT_INTERVAL = 60
DEFAULT_LOG_PATH = "/var/log/auth.log"


class SentinelSkill:
    """Autonomous security monitoring skill."""

    SKILL_NAME = "sentinel"

    ACTIONS = {
        "start_monitoring": "Start continuous log monitoring in the background",
        "stop_monitoring":  "Stop the background monitoring loop",
        "status":           "Get current sentinel status and threat summary",
        "analyze_logs":     "One-shot analysis of log content or log file",
        "block_ip":         "Block an IP address via iptables (requires confirm)",
    }

    def __init__(self):
        self._sentinel_id: Optional[str] = None
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._started_at: Optional[datetime] = None
        self._last_analysis: Optional[dict] = None
        self._threat_count = 0
        self._lock = asyncio.Lock()
        self._interval = DEFAULT_INTERVAL
        self._log_path = DEFAULT_LOG_PATH

    async def execute(self, action: str, params: dict, confirmed: bool = False) -> SkillResult:
        logger.info("[sentinel.execute] action=%s", action)
        try:
            dispatch = {
                "start_monitoring": self._start_monitoring,
                "stop_monitoring":  self._stop_monitoring,
                "status":           self._status,
                "analyze_logs":     self._analyze_logs,
                "block_ip":         self._block_ip,
            }
            fn = dispatch.get(action)
            if fn is None:
                return SkillResult.fail(f"SENTINEL_UNKNOWN_ACTION: '{action}'. Valid: {', '.join(dispatch)}")
            return await fn(params, confirmed)
        except Exception as exc:
            logger.error("[sentinel.exec_error] action=%s err=%s", action, exc)
            return SkillResult.fail(f"SENTINEL_INTERNAL_ERROR: {type(exc).__name__}: {exc}")

    async def _start_monitoring(self, params: dict, confirmed: bool) -> SkillResult:
        async with self._lock:
            if self._running:
                return SkillResult.fail("SENTINEL_ALREADY_RUNNING: Monitoring is already active")
            self._interval = int(params.get("interval", DEFAULT_INTERVAL))
            self._log_path = params.get("log_path", DEFAULT_LOG_PATH)
            self._sentinel_id = str(uuid.uuid4())[:8]
            self._running = True
            self._started_at = datetime.now(timezone.utc)
            self._threat_count = 0
            self._last_analysis = None
            self._task = asyncio.create_task(self._monitoring_loop())
            logger.info("[sentinel_start] id=%s interval=%ds", self._sentinel_id, self._interval)
            return SkillResult.ok({"running": True, "sentinel_id": self._sentinel_id, "interval": self._interval})

    async def _stop_monitoring(self, params: dict, confirmed: bool) -> SkillResult:
        async with self._lock:
            if not self._running:
                return SkillResult.fail("SENTINEL_NOT_RUNNING: No active monitoring")
            self._running = False
            if self._task and not self._task.done():
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            uptime = int((datetime.now(timezone.utc) - self._started_at).total_seconds()) if self._started_at else 0
            self._task = None
            return SkillResult.ok({"running": False, "sentinel_id": self._sentinel_id, "uptime_seconds": uptime, "threats_found": self._threat_count})

    async def _status(self, params: dict, confirmed: bool) -> SkillResult:
        async with self._lock:
            uptime = int((datetime.now(timezone.utc) - self._started_at).total_seconds()) if self._started_at else 0
            return SkillResult.ok({"running": self._running, "sentinel_id": self._sentinel_id, "interval": self._interval, "last_analysis": self._last_analysis, "threats_found": self._threat_count, "uptime_seconds": uptime})

    async def _analyze_logs(self, params: dict, confirmed: bool) -> SkillResult:
        log_content = params.get("log_content", "")
        log_path = params.get("log_path", self._log_path)
        if not log_content:
            log_content = await self._read_logs(log_path, 100)
        if not log_content or "Error" in log_content:
            return SkillResult.fail(f"SENTINEL_NO_LOGS: {log_path}")
        try:
            result = await self._run_security_pipeline(log_content)
            return SkillResult.ok(result)
        except Exception as exc:
            return SkillResult.fail(f"SENTINEL_ANALYSIS_FAIL: {exc}")

    async def _block_ip(self, params: dict, confirmed: bool) -> SkillResult:
        ip = params.get("ip", "").strip()
        duration = params.get("duration", "24h")
        if not ip:
            return SkillResult.fail("SENTINEL_NO_IP: ip is required")
        if not self._validate_ip(ip):
            return SkillResult.fail(f"SENTINEL_INVALID_IP: '{ip}'")
        admin_ip = os.getenv("ADMIN_IP", "127.0.0.1")
        if ip == admin_ip:
            return SkillResult.fail(f"SENTINEL_ADMIN_IP: Cannot block ADMIN_IP")
        if not confirmed:
            return SkillResult.confirm(f"Block IP {ip} via iptables for {duration}?", output={"ip": ip, "duration": duration})
        try:
            from core.skill_registry import SkillRegistry
            registry = SkillRegistry()
            check = await registry.execute("os_execution", "run_command", {"command": f"sudo iptables -L INPUT -n | grep {ip}", "timeout": 10}, confirmed=True)
            if check.success and check.output and check.output.get("stdout", "").strip():
                return SkillResult.ok({"status": "skipped", "reason": "IP already blocked", "ip": ip})
            block = await registry.execute("os_execution", "run_command", {"command": f"sudo iptables -I INPUT -s {ip} -j DROP", "timeout": 10}, confirmed=True)
            if block.success and block.output.get("returncode") == 0:
                return SkillResult.ok({"status": "success", "action": "blocked", "ip": ip, "duration": duration})
            return SkillResult.fail("SENTINEL_BLOCK_FAIL: iptables error")
        except Exception as exc:
            return SkillResult.fail(f"SENTINEL_BLOCK_FAIL: {exc}")

    async def _monitoring_loop(self):
        while self._running:
            try:
                log_content = await self._read_logs(self._log_path, 100)
                if log_content and "Error" not in log_content:
                    analysis = await self._run_security_pipeline(log_content)
                    async with self._lock:
                        self._last_analysis = analysis
                        if analysis.get("threat_detected"):
                            self._threat_count += 1
            except asyncio.CancelledError:
                break
            except Exception:
                pass
            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break

    async def _run_security_pipeline(self, log_content: str) -> dict:
        threat_analysis = await self._analyze_threats(log_content)
        if not threat_analysis.get("threat_detected"):
            return {"threat_detected": False, "message": "No threats detected"}
        validation = await self._validate_action(threat_analysis)
        if not validation.get("approved"):
            return {"threat_detected": True, **threat_analysis, "action_taken": "denied", "reason": validation.get("reason", "Action denied by policy")}
        return {"threat_detected": True, **threat_analysis, "action_taken": validation.get("action", "alert"), "reason": validation.get("reason", ""), "policy_matched": validation.get("policy_matched", "")}

    async def _analyze_threats(self, log_content: str) -> dict:
        system = "You are a security analyst. Analyze these authentication logs. Provide JSON with: threat_detected (bool), threat_type, source_ip, severity (critical|high|medium|low), evidence, recommended_action (block_ip|alert|monitor|none). JSON only."
        user = f"Logs:\n{log_content}"
        raw = await self._llm_call(system, user)
        raw = re.sub(r"```[a-z]*", "", raw).strip().strip("`").strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"threat_detected": False, "error": f"Parse failed: {raw[:200]}"}

    async def _validate_action(self, threat_analysis: dict) -> dict:
        rules = await self._read_rules()
        system = f"You are a security policy validator. ADMIN_IP: {os.getenv('ADMIN_IP', '127.0.0.1')}. MAX_FAILED_ATTEMPTS: {os.getenv('MAX_FAILED_ATTEMPTS', '5')}. Respond JSON with: approved (bool), reason, action (block_ip|alert|deny), confidence (0-1), policy_matched."
        user = f"THREAT:\n{json.dumps(threat_analysis, indent=2)}\n\nPOLICIES:\n{rules[:3000] if rules else 'No policy file'}"
        raw = await self._llm_call(system, user)
        raw = re.sub(r"```[a-z]*", "", raw).strip().strip("`").strip()
        try:
            validation = json.loads(raw)
            if threat_analysis.get("source_ip") == os.getenv("ADMIN_IP", "127.0.0.1"):
                validation["approved"] = False
                validation["reason"] = "Cannot action ADMIN_IP"
            return validation
        except json.JSONDecodeError:
            return {"approved": False, "reason": "Parse failed", "action": "deny"}

    async def _read_logs(self, log_path: str, num_lines: int = 100) -> str:
        try:
            from core.skill_registry import SkillRegistry
            registry = SkillRegistry()
            result = await registry.execute("os_execution", "run_command", {"command": f"tail -n {num_lines} {log_path}", "timeout": 10}, confirmed=True)
            if result.success:
                return result.output.get("stdout", "")
            return f"Error: {result.error}"
        except Exception as exc:
            return f"Error: {exc}"

    async def _read_rules(self) -> str:
        for path in [os.getenv("RULES_PATH", "rules.md"), "/app/rules.md", "./rules.md", "/app/output/sentinel-ai/rules.md"]:
            p = Path(path)
            if p.exists():
                return p.read_text(encoding="utf-8")
        return ""

    async def _llm_call(self, system: str, user: str, temperature: float = 0.1) -> str:
        try:
            from core.llm_router import LLMRouter, LLMConfig
            provider = os.getenv("LLM_PROVIDER", "deepseek")
            model = os.getenv("LLM_MODEL", "deepseek-chat")
            cfg = LLMConfig(provider=provider, model=model, temperature=temperature, max_tokens=2048)
            router = LLMRouter(cfg)
            return (await router.chat([{"role": "system", "content": system}, {"role": "user", "content": user}])).strip()
        except ImportError:
            import httpx
            api_key = os.getenv(f"{os.getenv('LLM_PROVIDER', 'deepseek').upper()}_API_KEY", "") or os.getenv("API_KEY", "")
            provider = os.getenv("LLM_PROVIDER", "deepseek")
            model = os.getenv("LLM_MODEL", "deepseek-chat")
            base_urls = {"openai": "https://api.openai.com/v1", "deepseek": "https://api.deepseek.com/v1"}
            base = base_urls.get(provider, base_urls["deepseek"])
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(f"{base}/chat/completions", json={"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "temperature": temperature, "max_tokens": 2048}, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"].strip()

    @staticmethod
    def _validate_ip(ip: str) -> bool:
        pattern = r"^(\d{1,3}\.){3}\d{1,3}$"
        if not re.match(pattern, ip):
            return False
        octets = ip.split(".")
        return all(0 <= int(o) <= 255 for o in octets)

    def describe(self) -> dict:
        return {"name": self.SKILL_NAME, "description": "Autonomous security monitoring", "actions": self.ACTIONS}
