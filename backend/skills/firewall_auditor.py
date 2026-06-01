"""
Skill: firewall_auditor
Category: ANALYSIS
Firewall rules analysis: parse iptables/nftables rules, detect over-permissive policies.
"""
from __future__ import annotations

import re
import subprocess

from core.skill_registry import SkillRegistry
from skills._base import Skill


class FirewallAuditorSkill(Skill):
    name             = "firewall_auditor"
    description      = "Firewall rules audit: iptables/nftables parser, over-permissive detection, egress check"
    usage            = "firewall_auditor <paste iptables/nftables rules OR host_ip>"
    trigger_patterns = ["firewall audit", "firewall rules", "iptables", "nftables", "firewall check"]

    DANGER_PATTERNS = [
        (re.compile(r"-s\s+0\.0\.0\.0/0.+--dport\s+22"),    "critical", "SSH open to 0.0.0.0/0"),
        (re.compile(r"-s\s+0\.0\.0\.0/0.+--dport\s+3389"),  "critical", "RDP open to 0.0.0.0/0"),
        (re.compile(r"-s\s+0\.0\.0\.0/0.+--dport\s+21"),    "high",     "FTP open to 0.0.0.0/0"),
        (re.compile(r"-s\s+0\.0\.0\.0/0.+--dport\s+23"),    "critical", "Telnet open to 0.0.0.0/0"),
        (re.compile(r"-j\s+ACCEPT\s+-s\s+0\.0\.0\.0/0"),    "high",     "Blanket ACCEPT from any source"),
        (re.compile(r"policy\s+ACCEPT", re.I),               "medium",   "Default ACCEPT policy"),
        (re.compile(r"-A\s+FORWARD\s+-j\s+ACCEPT"),         "high",     "Unrestricted forwarding"),
        (re.compile(r"--dport\s+445.+-j\s+ACCEPT"),         "high",     "SMB/445 exposed"),
        (re.compile(r"--dport\s+1433.+-j\s+ACCEPT"),        "high",     "MSSQL/1433 exposed"),
        (re.compile(r"--dport\s+5432.+-j\s+ACCEPT"),        "medium",   "PostgreSQL/5432 exposed"),
    ]

    def _get_live_rules(self, host: str) -> str | None:
        if host in ("localhost", "127.0.0.1", "::1"):
            for cmd in (["iptables", "-S"], ["nft", "list", "ruleset"]):
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                    if r.returncode == 0 and r.stdout.strip():
                        return r.stdout
                except Exception:
                    pass
        return None

    def run(self, args: str, mm=None) -> SkillResult:
        if not args.strip():
            return SkillResult(False, f"Usage: {self.usage}")

        rules_text = args.strip()
        if " " not in rules_text and "\n" not in rules_text:
            live = self._get_live_rules(rules_text)
            if live:
                rules_text = live
            else:
                return SkillResult(True,
                    f"**Firewall Auditor**\n\n"
                    f"  To audit rules: paste iptables -S output directly.\n"
                    f"  For {rules_text}: use `port_scanner {rules_text} 1-1024` to infer open ports,\n"
                    f"  then paste those results here for rule inference.")

        lines    = ["**Firewall Rules Audit**\n"]
        findings : list[tuple[str, str]] = []
        rule_lines = rules_text.strip().splitlines()
        lines.append(f"  Total rules parsed: {len(rule_lines)}")

        for pattern, severity, description in self.DANGER_PATTERNS:
            matching = [l for l in rule_lines if pattern.search(l)]
            if matching:
                findings.append((severity, f"{description} — {matching[0][:80]}"))

        egress_rules = [l for l in rule_lines if "OUTPUT" in l and "DROP" in l]
        if not egress_rules:
            findings.append(("medium", "No egress (OUTPUT) DROP rules found — unrestricted outbound"))

        accepts = sum(1 for l in rule_lines if "-j ACCEPT" in l or "accept" in l.lower())
        drops   = sum(1 for l in rule_lines if "-j DROP" in l or "-j REJECT" in l or "drop" in l.lower())
        lines.extend([
            f"  ACCEPT rules    : {accepts}",
            f"  DROP/REJECT     : {drops}",
        ])

        risk = ("critical" if any(s == "critical" for s, _ in findings) else
                "high"     if any(s == "high"     for s, _ in findings) else
                "medium"   if findings else "low")

        lines.extend([
            "",
            f"  **Risk Level : [{risk.upper()}]**",
            "",
            "  **Findings:**" if findings else "  ✓ No obvious over-permissive rules detected",
        ])
        for sev, desc in findings:
            lines.append(f"    [{sev.upper():<8}] {desc}")

        return SkillResult(True, "\n".join(lines))


def register(registry):
    registry.register("firewall_auditor", FirewallAuditorSkill())
