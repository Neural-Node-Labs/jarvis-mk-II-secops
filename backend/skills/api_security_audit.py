"""
Skill: api_security_audit
Category: ANALYSIS
Real API security audit: auth check, rate limit detection, endpoint exposure, CORS.
"""
from __future__ import annotations

import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from core.skill_registry import SkillRegistry
from skills._base import Skill


class ApiSecurityAuditSkill(Skill):
    name             = "api_security_audit"
    description      = "API security: auth check, rate limiting, CORS, info disclosure, common endpoints"
    usage            = "api_security_audit <base_url> [bearer_token]"
    trigger_patterns = ["api security", "api audit", "api scan", "rest api", "rate limiting"]

    COMMON_ENDPOINTS = [
        "/users", "/user", "/admin", "/api/users", "/api/v1/users",
        "/health", "/metrics", "/swagger.json", "/openapi.json",
        "/api-docs", "/.well-known/openid-configuration",
        "/actuator", "/actuator/env", "/graphql",
    ]

    def _req(self, url: str, headers: dict) -> tuple[int, dict, str]:
        try:
            req = urllib.request.Request(url, method="GET",
                headers={"User-Agent": "OMNIKON-SecOps/1.0.2", **headers})
            with urllib.request.urlopen(req, timeout=8) as r:
                return r.status, dict(r.headers), r.read(4096).decode(errors="replace")
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), ""
        except Exception:
            return 0, {}, ""

    def run(self, args: str, mm=None) -> SkillResult:
        parts = args.strip().split(None, 1)
        if not parts:
            return SkillResult(False, f"Usage: {self.usage}")
        base_url = parts[0].rstrip("/")
        token    = parts[1] if len(parts) > 1 else None
        if not base_url.startswith(("http://","https://")):
            base_url = "https://" + base_url
        auth_hdr = {"Authorization": f"Bearer {token}"} if token else {}

        lines    = [f"**API Security Audit** `{base_url}`\n"]
        findings : list[str] = []

        status, headers, body = self._req(base_url, auth_hdr)
        lines.append(f"  Baseline : HTTP {status}")
        if status == 0:
            return SkillResult(False, f"⚠ Cannot connect to {base_url}")

        if token:
            sc_noauth, _, _ = self._req(base_url, {})
            if sc_noauth == 200:
                findings.append("[CRITICAL] Endpoint accessible without authentication")
            elif sc_noauth in (401, 403):
                lines.append("  Auth     : ✓ Returns 401/403 without token")

        cors = headers.get("Access-Control-Allow-Origin",
                           headers.get("access-control-allow-origin", ""))
        if cors == "*":
            findings.append("[HIGH] CORS: Access-Control-Allow-Origin: * (any origin allowed)")
        elif cors:
            lines.append(f"  CORS     : {cors}")

        rl_headers = ["x-ratelimit-limit","ratelimit-limit","x-rate-limit",
                      "retry-after","x-request-limit"]
        rl_found = any(h in {k.lower() for k in headers} for h in rl_headers)
        if not rl_found:
            findings.append("[MEDIUM] No rate-limiting headers detected")
        else:
            lines.append("  Rate-limit: ✓ Headers present")

        exposed: list[str] = []
        def _probe(path: str) -> tuple[str, int]:
            sc, _, _ = self._req(f"{base_url}{path}", auth_hdr)
            return path, sc

        with ThreadPoolExecutor(max_workers=10) as ex:
            for path, sc in ex.map(_probe, self.COMMON_ENDPOINTS):
                if sc in (200, 201):
                    sev = "critical" if any(s in path for s in ("actuator","env","admin","graphql")) else "medium"
                    exposed.append(f"    [{sev.upper():<8}] HTTP {sc}  {path}")

        if exposed:
            findings.append(f"[HIGH] {len(exposed)} sensitive endpoint(s) accessible")
            lines.extend(["", "  **Exposed Endpoints:**"] + exposed)

        server  = headers.get("Server","")
        powered = headers.get("X-Powered-By","")
        if server:  findings.append(f"[LOW] Server header: {server}")
        if powered: findings.append(f"[LOW] X-Powered-By: {powered}")

        ct = headers.get("Content-Type","")
        if body.strip().startswith("{") and "application/json" not in ct.lower():
            findings.append("[LOW] JSON response without Content-Type: application/json")

        risk = ("critical" if any("[CRITICAL]" in f for f in findings) else
                "high"     if any("[HIGH]"     in f for f in findings) else
                "medium"   if findings else "low")

        lines.extend([
            "",
            f"  **Risk Level : [{risk.upper()}]**",
            f"  Issues       : {len(findings)}",
            "",
            "  **Findings:**" if findings else "  ✓ No critical API issues detected",
        ])
        lines.extend(f"    {f}" for f in findings)

        return SkillResult(True, "\n".join(lines))


def register(registry):
    registry.register("api_security_audit", ApiSecurityAuditSkill())
