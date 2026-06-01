"""
Skill: port_scanner
Category: NETWORK
Real TCP connect scan against a target — no nmap dependency.
"""
from __future__ import annotations

import socket
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.skill_registry import SkillRegistry, SkillResult
from skills._base import Skill, _well_known_service


class PortScannerSkill(Skill):
    name             = "port_scanner"
    description      = "TCP connect scan on host:ports — e.g. 192.168.1.1 22,80,443"
    usage            = "port_scanner <host> <ports>  (ports: 22,80,443 or 1-1024)"
    trigger_patterns = ["port scan", "scan ports", "open ports", "port check"]
    TIMEOUT          = 1.5
    MAX_PORTS        = 500

    def _resolve(self, host: str) -> str:
        try:
            return socket.gethostbyname(host)
        except socket.gaierror as e:
            raise ValueError(f"Cannot resolve {host}: {e}") from e

    def _parse_ports(self, spec: str) -> list[int]:
        ports: list[int] = []
        for part in spec.split(","):
            part = part.strip()
            if "-" in part:
                a, b = part.split("-", 1)
                ports.extend(range(int(a), int(b) + 1))
            else:
                ports.append(int(part))
        if len(ports) > self.MAX_PORTS:
            raise ValueError(f"Too many ports (max {self.MAX_PORTS})")
        return ports

    def _scan_port(self, ip: str, port: int) -> tuple[int, bool, str]:
        try:
            with socket.create_connection((ip, port), timeout=self.TIMEOUT) as s:
                s.settimeout(0.3)
                try:
                    banner = s.recv(256).decode(errors="replace").strip()[:80]
                except Exception:
                    banner = ""
                return port, True, banner
        except (ConnectionRefusedError, TimeoutError, OSError):
            return port, False, ""

    def run(self, args: str, mm=None) -> SkillResult:
        parts = args.strip().split(None, 1)
        if len(parts) < 2:
            return SkillResult(False, f"Usage: {self.usage}")
        host, port_spec = parts
        try:
            ip    = self._resolve(host)
            ports = self._parse_ports(port_spec)
        except ValueError as e:
            return SkillResult(False, f"⚠ {e}")

        open_ports: list[tuple[int, str]] = []
        with ThreadPoolExecutor(max_workers=50) as ex:
            futs = {ex.submit(self._scan_port, ip, p): p for p in ports}
            for fut in as_completed(futs):
                port, is_open, banner = fut.result()
                if is_open:
                    open_ports.append((port, banner))

        open_ports.sort()
        if not open_ports:
            out = f"**Port Scan** {host} ({ip}) — no open ports found in {port_spec}"
            return SkillResult(True, out)

        lines = [f"**Port Scan** {host} ({ip}) — {len(open_ports)} open port(s)\n"]
        for port, banner in open_ports:
            svc = _well_known_service(port)
            b   = f" — `{banner}`" if banner else ""
            lines.append(f"  {port:5d}/tcp  OPEN  {svc}{b}")
        return SkillResult(True, "\n".join(lines))


def register(registry):
    registry.register("port_scanner", PortScannerSkill())
