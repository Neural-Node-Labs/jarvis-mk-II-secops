"""
Skill: web_app_scanner
Category: ANALYSIS
Active OWASP Top 10 web application scan — real HTTP probes.
"""
from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request

from core.skill_registry import SkillRegistry, SkillResult
from skills._base import Skill


class WebAppScannerSkill(Skill):
    name             = "web_app_scanner"
    description      = "OWASP Top 10 active scan: SQLi probe, XSS probe, headers, info disclosure"
    usage            = "web_app_scanner <url> [auth_header]"
    trigger_patterns = ["web app scan", "owasp scan", "web scan", "xss scan", "sqli scan"]

    SQLI_PAYLOADS = ["'", "\"", "' OR '1'='1", "1; DROP TABLE users--"]
    XSS_PAYLOADS  = ["<script>alert(1)</script>", "\"><img src=x onerror=alert(1)>"]

    def _req(self, url: str, headers: dict | None = None, path: str = "") -> tuple[int, dict, str]:
        full_url = url.rstrip("/") + path if path else url
        try:
            req = urllib.request.Request(full_url, method="GET",
                headers={"User-Agent": "OMNIKON-SecOps/1.0.2", **(headers or {})})
            with urllib.request.urlopen(req, timeout=8) as r:
                return r.status, dict(r.headers), r.read(8192).decode(errors="replace")
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), ""
        except Exception:
            return 0, {}, ""

    def run(self, args: str, mm=None) -> SkillResult:
        parts = args.strip().split(None, 1)
        if not parts:
            return SkillResult(False, f"Usage: {self.usage}")
        url         = parts[0]
        auth_header = parts[1] if len(parts) > 1 else None
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        extra = {"Authorization": auth_header} if auth_header else {}

        findings: list[dict] = []
        lines = [f"**Web Application Scan** `{url}`\n"]

        status, resp_headers, body = self._req(url, extra)
        if status == 0:
            return SkillResult(False, f"⚠ Cannot connect to {url}")
        lines.append(f"  Baseline : HTTP {status}")

        SEC_HDRS = ["strict-transport-security","content-security-policy",
                    "x-frame-options","x-content-type-options"]
        missing_hdrs = [h for h in SEC_HDRS
                        if h not in {k.lower(): v for k, v in resp_headers.items()}]
        if missing_hdrs:
            findings.append({"type": "A05:MissingHeaders", "severity": "medium",
                             "detail": f"Missing: {', '.join(missing_hdrs)}"})

        server  = resp_headers.get("Server",       resp_headers.get("server", ""))
        powered = resp_headers.get("X-Powered-By", resp_headers.get("x-powered-by", ""))
        if server:  findings.append({"type": "A05:InfoDisclosure", "severity": "low",
                                     "detail": f"Server header: {server}"})
        if powered: findings.append({"type": "A05:InfoDisclosure", "severity": "low",
                                      "detail": f"X-Powered-By: {powered}"})

        for payload in self.SQLI_PAYLOADS[:2]:
            test_url = f"{url}?id={urllib.parse.quote(payload)}"
            sc, _, bd = self._req(test_url, extra)
            if any(ind in bd.lower() for ind in
                   ["sql syntax","mysql_fetch","ora-","postgresql",
                    "unclosed quotation","sqlstate","syntax error"]):
                findings.append({"type": "A03:SQLi", "severity": "critical",
                                 "detail": f"SQL error returned for payload: {payload[:30]}"})
                break

        for payload in self.XSS_PAYLOADS[:1]:
            test_url = f"{url}?q={urllib.parse.quote(payload)}"
            sc, _, bd = self._req(test_url, extra)
            if payload.lower().replace(" ", "") in bd.lower().replace(" ", ""):
                findings.append({"type": "A03:XSS", "severity": "high",
                                 "detail": "Reflected XSS: payload echoed back"})
                break

        for path in ["/.env", "/admin", "/phpinfo.php", "/.git/HEAD",
                     "/config.php", "/wp-admin", "/api/v1/users"]:
            sc, _, bd = self._req(url, extra, path)
            if sc in (200, 403) and sc != 404:
                sev = "high" if path in ("/.env","/.git/HEAD","/.git/config") else "medium"
                findings.append({"type": "A05:SensitivePath", "severity": sev,
                                 "detail": f"HTTP {sc} at {path}"})

        test_url = f"{url}?redirect=https://evil.example.com"
        sc, rdr_hdrs, _ = self._req(test_url, extra)
        location = rdr_hdrs.get("Location", rdr_hdrs.get("location", ""))
        if sc in (301,302,307,308) and "evil.example.com" in location:
            findings.append({"type": "A01:OpenRedirect", "severity": "high",
                             "detail": "Open redirect to attacker-controlled URL"})

        critical = sum(1 for f in findings if f["severity"] == "critical")
        high     = sum(1 for f in findings if f["severity"] == "high")
        risk     = ("critical" if critical > 0 else
                    "high"     if high > 0 else
                    "medium"   if findings else "low")

        lines.extend([
            f"  Risk Level : [{risk.upper()}]",
            f"  Findings   : {len(findings)} ({critical} critical, {high} high)",
            "",
            "  **Findings:**" if findings else "  ✓ No critical vulnerabilities detected",
        ])
        for f in findings:
            lines.append(f"    [{f['severity'].upper():<8}] {f['type']:<25} {f['detail']}")

        return SkillResult(True, "\n".join(lines))


def register(registry):
    registry.register("web_app_scanner", WebAppScannerSkill())
