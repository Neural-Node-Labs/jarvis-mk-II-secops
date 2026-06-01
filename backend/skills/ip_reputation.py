"""
Skill: ip_reputation
Category: THREAT
Real IP reputation check via AbuseIPDB + DNSBL.
Requires ABUSEIPDB_API_KEY env var for live scoring (optional).
"""
from __future__ import annotations

import ipaddress
import json
import os
import socket
import urllib.request

from core.skill_registry import SkillRegistry, SkillResult
from skills._base import Skill


class IpReputationSkill(Skill):
    name             = "ip_reputation"
    description      = "IP reputation: AbuseIPDB + DNSBL checks (set ABUSEIPDB_API_KEY)"
    usage            = "ip_reputation <ip_address>"
    trigger_patterns = ["ip reputation", "is this ip malicious", "check ip", "ip abuse"]

    DNSBLS = [
        "zen.spamhaus.org",
        "bl.spamcop.net",
        "dnsbl.sorbs.net",
        "xbl.spamhaus.org",
    ]

    def _dnsbl_check(self, ip: str) -> list[str]:
        try:
            parts = ipaddress.ip_address(ip).reverse_pointer.replace(".in-addr.arpa", "")
        except ValueError:
            return []
        listed_on = []
        for bl in self.DNSBLS:
            query = f"{parts}.{bl}"
            try:
                socket.getaddrinfo(query, None)
                listed_on.append(bl)
            except socket.gaierror:
                pass
        return listed_on

    def run(self, args: str, mm=None) -> SkillResult:
        ip = args.strip()
        if not ip:
            return SkillResult(False, f"Usage: {self.usage}")
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            return SkillResult(False, f"⚠ Invalid IP: {ip}")

        lines = [f"**IP Reputation** `{ip}`\n"]

        api_key = os.getenv("ABUSEIPDB_API_KEY", "")
        if api_key:
            try:
                url = f"https://api.abuseipdb.com/api/v2/check?ipAddress={ip}&maxAgeInDays=90"
                req = urllib.request.Request(url, headers={
                    "Key": api_key, "Accept": "application/json",
                    "User-Agent": "OMNIKON-SecOps/1.0.2"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    d    = json.loads(resp.read())["data"]
                    conf = d.get("abuseConfidenceScore", 0)
                    rpts = d.get("totalReports",         0)
                    ctry = d.get("countryCode",          "??")
                    isp  = d.get("isp",                  "unknown")
                    tag  = "[CRITICAL]" if conf >= 75 else ("[WARN]" if conf >= 25 else "[CLEAN]")
                    lines += [
                        f"  AbuseIPDB: {tag} confidence={conf}%  reports={rpts}",
                        f"  Country  : {ctry}  ISP: {isp}",
                    ]
            except Exception as e:
                lines.append(f"  AbuseIPDB: unavailable ({e})")
        else:
            lines.append("  AbuseIPDB: set ABUSEIPDB_API_KEY for live scoring")

        listed = self._dnsbl_check(ip)
        if listed:
            lines.append(f"  [CRITICAL] DNSBL listed on: {', '.join(listed)}")
        else:
            lines.append(f"  DNSBL: not listed on {len(self.DNSBLS)} checked blocklists")

        return SkillResult(True, "\n".join(lines))


def register(registry):
    registry.register("ip_reputation", IpReputationSkill())
