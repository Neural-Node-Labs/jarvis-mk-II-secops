"""
Skill: whois_lookup
Category: NETWORK
Real WHOIS query via TCP port 43.
"""
from __future__ import annotations

import socket

from core.skill_registry import SkillRegistry
from skills._base import Skill, _is_ip


class WhoisLookupSkill(Skill):
    name             = "whois_lookup"
    description      = "WHOIS registration data for domain or IP (real WHOIS TCP query)"
    usage            = "whois_lookup <domain|ip>"
    trigger_patterns = ["whois", "domain registration", "ip owner", "registrar"]
    WHOIS_SERVERS    = {
        "default": "whois.iana.org",
        "com": "whois.verisign-grs.com",
        "net": "whois.verisign-grs.com",
        "org": "whois.pir.org",
        "io":  "whois.nic.io",
        "uk":  "whois.nic.uk",
        "de":  "whois.denic.de",
    }

    def _whois_query(self, target: str, server: str) -> str:
        with socket.create_connection((server, 43), timeout=10) as s:
            s.sendall(f"{target}\r\n".encode())
            chunks = []
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        return b"".join(chunks).decode(errors="replace")

    def run(self, args: str, mm=None) -> SkillResult:
        target = args.strip()
        if not target:
            return SkillResult(False, f"Usage: {self.usage}")

        if _is_ip(target):
            server = "whois.arin.net"
        else:
            tld    = target.rsplit(".", 1)[-1].lower()
            server = self.WHOIS_SERVERS.get(tld, self.WHOIS_SERVERS["default"])

        try:
            raw = self._whois_query(target, server)
            if server == "whois.iana.org":
                for line in raw.splitlines():
                    if line.strip().lower().startswith("whois:"):
                        refer = line.split(":", 1)[1].strip()
                        try:
                            raw = self._whois_query(target, refer)
                        except Exception:
                            pass
                        break

            important = {}
            for line in raw.splitlines():
                for key in ("Registrar", "Registrant", "Creation Date", "Expiry Date",
                            "Updated Date", "Name Server", "Status", "NetRange",
                            "Organization", "OrgName", "Country"):
                    if line.strip().lower().startswith(key.lower() + ":"):
                        val = line.split(":", 1)[1].strip()
                        if key not in important:
                            important[key] = val

            lines = [f"**WHOIS** `{target}` (via {server})\n"]
            for k, v in important.items():
                lines.append(f"  {k:<20}: {v}")
            if not important:
                lines.append(raw[:800])

            return SkillResult(True, "\n".join(lines))
        except (socket.timeout, ConnectionRefusedError) as e:
            return SkillResult(False, f"⚠ WHOIS connection failed: {e}")
        except Exception as e:
            return SkillResult(False, f"⚠ {e}")


def register(registry):
    registry.register("whois_lookup", WhoisLookupSkill())
