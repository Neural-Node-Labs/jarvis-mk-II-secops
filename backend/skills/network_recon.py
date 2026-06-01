"""
Skill: network_recon
Category: NETWORK
Network reconnaissance: CIDR sweep, host discovery, OS hints.
"""
from __future__ import annotations

import ipaddress
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor

from core.skill_registry import SkillRegistry
from skills._base import Skill, _well_known_service


class NetworkReconSkill(Skill):
    name             = "network_recon"
    description      = "Network recon: CIDR host discovery, open service sweep, topology mapping"
    usage            = "network_recon <cidr_or_host>  e.g. 192.168.1.0/24 or 10.0.0.1"
    trigger_patterns = ["network recon", "network scan", "host discovery", "cidr scan", "topology map"]
    MAX_HOSTS        = 64

    def _ping(self, ip: str) -> bool:
        try:
            r = subprocess.run(
                ["ping", "-c", "1", "-W", "1", str(ip)],
                capture_output=True, timeout=3
            )
            return r.returncode == 0
        except Exception:
            return False

    def _tcp_probe(self, ip: str, ports: list[int] = None) -> list[int]:
        ports = ports or [22, 80, 443, 445, 3389]
        open_p = []
        for port in ports:
            try:
                with socket.create_connection((str(ip), port), timeout=0.8):
                    open_p.append(port)
            except Exception:
                pass
        return open_p

    def run(self, args: str, mm=None) -> SkillResult:
        target = args.strip()
        if not target:
            return SkillResult(False, f"Usage: {self.usage}")

        try:
            network = ipaddress.ip_network(target, strict=False)
            hosts   = list(network.hosts())[:self.MAX_HOSTS]
        except ValueError:
            try:
                ip    = socket.gethostbyname(target)
                hosts = [ipaddress.ip_address(ip)]
            except Exception as e:
                return SkillResult(False, f"⚠ Cannot parse target: {e}")

        lines = [f"**Network Recon** `{target}` — scanning {len(hosts)} host(s)\n"]
        live_hosts: list[dict] = []

        def _probe_host(ip: ipaddress.IPv4Address) -> dict | None:
            ip_str     = str(ip)
            open_ports = self._tcp_probe(ip_str)
            if not open_ports:
                if not self._ping(ip_str):
                    return None
            try:
                hostname, _, _ = socket.gethostbyaddr(ip_str)
            except Exception:
                hostname = ""
            return {"ip": ip_str, "hostname": hostname, "open_ports": open_ports}

        with ThreadPoolExecutor(max_workers=30) as ex:
            for result in ex.map(_probe_host, hosts):
                if result:
                    live_hosts.append(result)

        if not live_hosts:
            lines.append("  No live hosts discovered.")
        else:
            lines.append(f"  **Live Hosts ({len(live_hosts)}):**")
            for h in live_hosts[:30]:
                hostname = f" ({h['hostname']})" if h["hostname"] else ""
                ports    = ", ".join(str(p) for p in h["open_ports"])
                services = ", ".join(_well_known_service(p) for p in h["open_ports"])
                lines.append(f"    {h['ip']:<17}{hostname}")
                if h["open_ports"]:
                    lines.append(f"      Services: {services} (ports {ports})")

        total_services = sum(len(h["open_ports"]) for h in live_hosts)
        risk = "high" if any(p in [23, 21, 445, 3389]
                             for h in live_hosts for p in h["open_ports"]) else "medium"
        lines.extend([
            "",
            "  **Topology Summary:**",
            f"    Live hosts      : {len(live_hosts)}/{len(hosts)}",
            f"    Exposed services: {total_services}",
            f"    Risk level      : [{risk.upper()}]",
        ])
        return SkillResult(True, "\n".join(lines))


def register(registry):
    registry.register("network_recon", NetworkReconSkill())
