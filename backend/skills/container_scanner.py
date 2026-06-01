"""
Skill: container_scanner
Category: CLOUD
Container security scan: Dockerfile analysis, image inspection, secret detection.
"""
from __future__ import annotations

import json
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

from core.skill_registry import SkillRegistry
from skills._base import Skill


class ContainerScannerSkill(Skill):
    name             = "container_scanner"
    description      = "Container security: Docker image inspect, Dockerfile audit, secret scan, privilege check"
    usage            = "container_scanner <image:tag OR Dockerfile_path>"
    trigger_patterns = ["container scan", "docker scan", "image scan", "container security", "dockerfile"]

    SECRET_PATTERNS = [
        (re.compile(r'(?i)(password|passwd|secret|api_key|token)\s*[=:]\s*["\']?[^\s"\']{8,}'), "secret"),
        (re.compile(r'(?i)AWS_ACCESS_KEY_ID'),                                                   "aws_key"),
        (re.compile(r'(?i)PRIVATE_KEY'),                                                         "private_key"),
        (re.compile(r'(?i)(GITHUB_TOKEN|GH_TOKEN)'),                                             "github_token"),
    ]
    DOCKERFILE_RISKS = [
        (re.compile(r'^FROM\s+.*:latest', re.M | re.I),  "medium",   "Using :latest tag (unpinned)"),
        (re.compile(r'^USER\s+root',      re.M | re.I),  "critical", "Running as root user"),
        (re.compile(r'^RUN\s+.*curl.+\|',re.M | re.I),  "high",     "Curl-pipe pattern (supply chain risk)"),
        (re.compile(r'ADD\s+http',        re.M | re.I),  "high",     "ADD from remote URL (no checksum)"),
        (re.compile(r'chmod\s+777',       re.M | re.I),  "high",     "chmod 777 — world-writable"),
        (re.compile(r'--privileged',      re.M | re.I),  "critical", "--privileged flag detected"),
    ]

    def _inspect_docker(self, image: str) -> dict | None:
        try:
            r = subprocess.run(["docker", "inspect", image],
                               capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                return json.loads(r.stdout)[0]
        except Exception:
            pass
        return None

    def _scan_dockerfile(self, content: str) -> list[tuple[str, str]]:
        findings = []
        for pattern, severity, description in self.DOCKERFILE_RISKS:
            if pattern.search(content):
                findings.append((severity, description))
        for pattern, secret_type in self.SECRET_PATTERNS:
            if pattern.findall(content):
                findings.append(("critical", f"Potential secret in Dockerfile: {secret_type}"))
        return findings

    def run(self, args: str, mm=None) -> SkillResult:
        target = args.strip()
        if not target:
            return SkillResult(False, f"Usage: {self.usage}")

        lines    = [f"**Container Security Scan** `{target}`\n"]
        findings : list[tuple[str, str]] = []

        df_path = Path(target)
        if df_path.exists() and df_path.is_file():
            content = df_path.read_text(errors="replace")
            lines.append(f"  Source  : Dockerfile ({df_path})")
            lines.append(f"  Lines   : {len(content.splitlines())}")
            findings.extend(self._scan_dockerfile(content))
            from_line = next((l for l in content.splitlines()
                               if l.strip().upper().startswith("FROM")), "")
            if from_line:
                lines.append(f"  Base    : {from_line.strip()}")
        else:
            inspect = self._inspect_docker(target)
            if inspect:
                cfg        = inspect.get("Config", {})
                user       = cfg.get("User", "root")
                expose     = list(cfg.get("ExposedPorts", {}).keys())
                host_cfg   = inspect.get("HostConfig", {})
                privileged = host_cfg.get("Privileged", False)
                lines.extend([
                    f"  Image   : {target}",
                    f"  User    : {user or 'root (default)'}",
                    f"  Exposed : {', '.join(expose[:10]) if expose else 'none'}",
                ])
                if not user or user == "root":
                    findings.append(("critical", "Container runs as root"))
                if privileged:
                    findings.append(("critical", "--privileged mode enabled"))
                for e in cfg.get("Env", []):
                    for pattern, secret_type in self.SECRET_PATTERNS:
                        if pattern.search(e):
                            findings.append(("critical", f"Secret in ENV: {secret_type} ({e[:40]})"))
                if any("0.0.0.0" in str(p) for p in expose):
                    findings.append(("medium", "Ports bound to 0.0.0.0"))
            else:
                image_name = target.split(":")[0].split("/")[-1]
                lines.append(f"  Docker not available — performing CVE lookup for '{image_name}'")
                try:
                    url = (f"https://services.nvd.nist.gov/rest/json/cves/2.0"
                           f"?keywordSearch={urllib.parse.quote(image_name)}&resultsPerPage=5")
                    req = urllib.request.Request(url, headers={"User-Agent":"OMNIKON-SecOps/1.0.2"})
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        data  = json.loads(resp.read())
                        vulns = data.get("vulnerabilities", [])
                        if vulns:
                            findings.append(("info", f"{len(vulns)} CVEs found for '{image_name}' in NVD"))
                            for v in vulns[:3]:
                                findings.append(("medium", f"NVD: {v['cve']['id']}"))
                except Exception:
                    lines.append("  NVD lookup failed — check connectivity")

        risk = ("critical" if any(s == "critical" for s, _ in findings) else
                "high"     if any(s == "high"     for s, _ in findings) else
                "medium"   if findings else "low")

        lines.extend([
            "",
            f"  **Risk Level : [{risk.upper()}]**",
            "",
            "  **Findings:**" if findings else "  ✓ No critical container issues detected",
        ])
        for sev, desc in findings:
            lines.append(f"    [{sev.upper():<8}] {desc}")

        return SkillResult(True, "\n".join(lines))


def register(registry):
    registry.register("container_scanner", ContainerScannerSkill())
