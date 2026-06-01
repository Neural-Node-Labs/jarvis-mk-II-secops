"""
Skill: dns_security
Category: NETWORK
Full DNS security analysis: DNSSEC, zone transfer, SPF/DKIM/DMARC.
"""
from __future__ import annotations

import socket
import subprocess

from core.skill_registry import SkillRegistry, SkillResult
from skills._base import Skill


class DnsSecuritySkill(Skill):
    name             = "dns_security"
    description      = "DNS security: DNSSEC, zone transfer, SPF/DKIM/DMARC, MX/NS/TXT records"
    usage            = "dns_security <domain>"
    trigger_patterns = ["dns security", "dnssec", "zone transfer", "email security",
                        "spf dkim dmarc", "subdomain"]

    def _dig(self, domain: str, rec_type: str) -> list[str]:
        try:
            r = subprocess.run(["dig", "+short", rec_type, domain],
                               capture_output=True, text=True, timeout=10)
            return [l.strip() for l in r.stdout.strip().splitlines() if l.strip()]
        except Exception:
            return []

    def run(self, args: str, mm=None) -> SkillResult:
        domain = args.strip().lower()
        if not domain:
            return SkillResult(False, f"Usage: {self.usage}")

        lines = [f"**DNS Security Analysis** `{domain}`\n"]

        a_recs = self._dig(domain, "A") or (
            [socket.gethostbyname(domain)] if not None else [])
        lines.append(f"  A records    : {', '.join(a_recs) if a_recs else 'none'}")

        mx_recs = self._dig(domain, "MX")
        lines.append(f"  MX records   : {', '.join(mx_recs[:3]) if mx_recs else 'none'}")

        ns_recs = self._dig(domain, "NS")
        lines.append(f"  NS records   : {', '.join(ns_recs[:3]) if ns_recs else 'none'}")

        # SPF / DMARC / DKIM
        txt_recs  = self._dig(domain, "TXT")
        spf       = next((r for r in txt_recs if "v=spf1" in r.lower()), None)
        dmarc_recs= self._dig(f"_dmarc.{domain}", "TXT")
        dmarc     = next((r for r in dmarc_recs if "v=dmarc1" in r.lower()), None)
        dkim_recs = self._dig(f"default._domainkey.{domain}", "TXT")
        dkim      = next((r for r in dkim_recs if "v=dkim1" in r.lower()), None)

        lines.extend([
            "",
            "  **Email Security:**",
            f"    SPF   : {'✓ ' + spf[:80]  if spf  else '✗ [WARN] Missing SPF record'}",
            f"    DMARC : {'✓ ' + dmarc[:80] if dmarc else '✗ [WARN] Missing DMARC record'}",
            f"    DKIM  : {'✓ present'        if dkim  else '⚠ default selector not found'}",
        ])

        # Zone transfer
        zt_vulnerable = False
        if ns_recs:
            ns_host = ns_recs[0].rstrip(".")
            try:
                r = subprocess.run(["dig", "+short", "AXFR", domain, f"@{ns_host}"],
                                   capture_output=True, text=True, timeout=8)
                if r.stdout.strip() and len(r.stdout.strip().splitlines()) > 3:
                    zt_vulnerable = True
            except Exception:
                pass

        lines.append(f"\n  Zone Transfer: "
                     f"{'[CRITICAL] ALLOWED — data exposed' if zt_vulnerable else '✓ Restricted'}")

        dnssec_recs = self._dig(domain, "DNSKEY")
        lines.append(f"  DNSSEC       : {'✓ Enabled' if dnssec_recs else '✗ [INFO] Not enabled'}")

        issues = []
        if not spf:        issues.append("no SPF")
        if not dmarc:      issues.append("no DMARC")
        if zt_vulnerable:  issues.append("zone transfer allowed")
        risk = "high" if zt_vulnerable else ("medium" if issues else "low")
        lines.extend([
            "",
            f"  **Risk Level : [{risk.upper()}]**",
            f"  Issues       : {', '.join(issues) if issues else 'none detected'}",
        ])
        return SkillResult(True, "\n".join(lines))


def register(registry):
    registry.register("dns_security", DnsSecuritySkill())
