"""
Skill: http_header_analyzer
Category: NETWORK
Real HTTP/HTTPS header fetch and security analysis.
"""
from __future__ import annotations

import urllib.error
import urllib.request

from core.skill_registry import SkillRegistry, SkillResult
from skills._base import Skill


class HttpHeaderAnalyzerSkill(Skill):
    name             = "http_header_analyzer"
    description      = "Fetch HTTP headers and audit security posture (HSTS, CSP, X-Frame, etc.)"
    usage            = "http_header_analyzer <url>"
    trigger_patterns = ["http headers", "security headers", "check hsts", "header analysis"]

    SECURITY_HEADERS = {
        "Strict-Transport-Security": ("HSTS",       True),
        "Content-Security-Policy":   ("CSP",        True),
        "X-Frame-Options":           ("X-Frame",    True),
        "X-Content-Type-Options":    ("XCTO",       True),
        "Referrer-Policy":           ("Ref-Policy", True),
        "Permissions-Policy":        ("Perm-Policy",False),
        "X-XSS-Protection":          ("XSS-Prot",  False),
    }

    def run(self, args: str, mm=None) -> SkillResult:
        url = args.strip()
        if not url:
            return SkillResult(False, f"Usage: {self.usage}")
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        try:
            req = urllib.request.Request(url, method="HEAD",
                headers={"User-Agent": "OMNIKON-SecOps/1.0.2"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                headers = dict(resp.headers)
                status  = resp.status
                final   = resp.url

            lines = [f"**HTTP Header Analysis** `{url}`\n",
                     f"  Status  : {status}",
                     f"  Server  : {headers.get('server', 'hidden')}",
                     f"  Powered : {headers.get('x-powered-by', 'hidden')}",
                     ""]
            if final != url:
                lines.insert(2, f"  Final   : {final}")

            missing_critical = []
            lines.append("  Security Headers:")
            for hdr, (label, critical) in self.SECURITY_HEADERS.items():
                val     = headers.get(hdr, headers.get(hdr.lower(), None))
                present = val is not None
                flag    = "✓" if present else ("✗ [CRITICAL]" if critical else "✗ [INFO]")
                display = val[:80] if val else "absent"
                lines.append(f"    {flag} {label:<15} {display}")
                if not present and critical:
                    missing_critical.append(label)

            if missing_critical:
                lines.append(f"\n  [CRITICAL] Missing: {', '.join(missing_critical)}")

            return SkillResult(True, "\n".join(lines))

        except urllib.error.URLError as e:
            return SkillResult(False, f"⚠ Request failed: {e}")
        except Exception as e:
            return SkillResult(False, f"⚠ {e}")


def register(registry):
    registry.register("http_header_analyzer", HttpHeaderAnalyzerSkill())
