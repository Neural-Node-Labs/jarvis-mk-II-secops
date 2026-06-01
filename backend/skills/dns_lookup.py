"""
Skill: dns_lookup
Category: NETWORK
Real DNS resolution using stdlib socket — A, AAAA, MX, NS, TXT via dig fallback.
"""
from __future__ import annotations

import socket
import subprocess

from core.skill_registry import SkillRegistry, SkillResult
from skills._base import Skill, _is_ip


class DnsLookupSkill(Skill):
    name             = "dns_lookup"
    description      = "DNS resolution: A, AAAA, MX, TXT, NS, PTR, reverse lookup"
    usage            = "dns_lookup <hostname|ip> [type]  type=A|AAAA|MX|TXT|NS|PTR"
    trigger_patterns = ["dns lookup", "resolve hostname", "dns record", "nslookup"]

    def run(self, args: str, mm=None) -> SkillResult:
        parts = args.strip().split()
        if not parts:
            return SkillResult(False, f"Usage: {self.usage}")
        target   = parts[0]
        rec_type = parts[1].upper() if len(parts) > 1 else "A"
        lines    = [f"**DNS Lookup** `{target}` ({rec_type})\n"]

        try:
            if rec_type == "PTR" or _is_ip(target):
                hostname, aliases, _ = socket.gethostbyaddr(target)
                lines.append(f"  PTR  {hostname}")
                for a in aliases:
                    lines.append(f"  ALT  {a}")
            elif rec_type in ("A", "AAAA"):
                family = socket.AF_INET6 if rec_type == "AAAA" else socket.AF_INET
                infos  = socket.getaddrinfo(target, None, family)
                seen   = set()
                for info in infos:
                    ip = info[4][0]
                    if ip not in seen:
                        lines.append(f"  {rec_type:<5} {ip}")
                        seen.add(ip)
            else:
                result = subprocess.run(
                    ["dig", "+short", rec_type, target],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode != 0 or not result.stdout.strip():
                    lines.append(f"  No {rec_type} records found (or dig not available)")
                else:
                    for rec in result.stdout.strip().splitlines():
                        lines.append(f"  {rec_type:<5} {rec.strip()}")
        except (socket.herror, socket.gaierror) as e:
            return SkillResult(False, f"⚠ DNS error: {e}")
        except FileNotFoundError:
            lines.append("  ⚠ dig not installed; only A/AAAA/PTR available via stdlib")
        except subprocess.TimeoutExpired:
            return SkillResult(False, "⚠ DNS query timed out")

        return SkillResult(True, "\n".join(lines))


def register(registry):
    registry.register("dns_lookup", DnsLookupSkill())
