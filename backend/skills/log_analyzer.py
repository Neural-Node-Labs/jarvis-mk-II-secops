"""
Skill: log_analyzer
Category: ANALYSIS
Deep log analysis: brute-force, injections, anomalies, timeline.
"""
from __future__ import annotations

import re

from core.skill_registry import SkillRegistry, SkillResult
from skills._base import Skill


class LogAnalyzerSkill(Skill):
    name             = "log_analyzer"
    description      = "Deep log analysis: brute-force, injections, recon, timeline correlation"
    usage            = "log_analyzer <log text>"
    trigger_patterns = ["analyze log", "parse log", "check logs", "log analysis", "siem"]

    _IP     = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
    _TS     = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?")
    _STATUS = re.compile(r"\b([1-5]\d{2})\b")
    _SQL_INJ= re.compile(r"(?i)(UNION\s+SELECT|OR\s+1=1|DROP\s+TABLE|INSERT\s+INTO|--\s|xp_cmdshell)")
    _XSS    = re.compile(r"(?i)(<script|javascript:|onerror=|onload=|alert\()")
    _PATH_T = re.compile(r"(?i)(\.\.\/|\.\.\\|%2e%2e|\/etc\/passwd|\/proc\/)")
    _SCANNER= re.compile(r"(?i)(nikto|nmap|sqlmap|masscan|zap|burpsuite|nessus|openvas|nuclei)")

    def run(self, args: str, mm=None) -> SkillResult:
        if not args.strip():
            return SkillResult(False, f"Usage: {self.usage}")

        lines      = args.strip().splitlines()
        ips        = sorted(set(self._IP.findall(args)))
        timestamps = sorted(set(self._TS.findall(args)))

        ip_failures:  dict[str, int] = {}
        ip_successes: dict[str, int] = {}
        for line in lines:
            line_ips = self._IP.findall(line)
            if re.search(r"(?i)(FAILED_LOGIN|authentication failure|invalid password|unauthorized)", line):
                for ip in line_ips:
                    ip_failures[ip] = ip_failures.get(ip, 0) + 1
            if re.search(r"(?i)(LOGIN_SUCCESS|authenticated|session opened)", line):
                for ip in line_ips:
                    ip_successes[ip] = ip_successes.get(ip, 0) + 1

        sqli_lines   = [l for l in lines if self._SQL_INJ.search(l)]
        xss_lines    = [l for l in lines if self._XSS.search(l)]
        path_lines   = [l for l in lines if self._PATH_T.search(l)]
        scanner_hits = [l for l in lines if self._SCANNER.search(l)]

        status_counts: dict[str, int] = {}
        for s in self._STATUS.findall(args):
            status_counts[s] = status_counts.get(s, 0) + 1

        total_reqs = sum(status_counts.values())
        errors_4xx = sum(v for k, v in status_counts.items() if k.startswith("4"))
        errors_5xx = sum(v for k, v in status_counts.items() if k.startswith("5"))

        findings = []
        for ip, cnt in ip_failures.items():
            tag       = "[CRITICAL]" if cnt >= 5 else "[WARN]"
            succ_note = f" ({ip_successes.get(ip, 0)} success after)" if ip in ip_successes else ""
            findings.append(f"{tag} Brute-force: {cnt} failures from {ip}{succ_note}")

        if sqli_lines:
            findings.append(f"[CRITICAL] SQL Injection attempts: {len(sqli_lines)} lines")
            findings.append(f"  Sample: {sqli_lines[0][:120]}")
        if xss_lines:
            findings.append(f"[CRITICAL] XSS attempts: {len(xss_lines)} lines")
        if path_lines:
            findings.append(f"[HIGH] Path traversal attempts: {len(path_lines)} lines")
        if scanner_hits:
            scanners = set(self._SCANNER.search(l).group(1) for l in scanner_hits if self._SCANNER.search(l))
            findings.append(f"[WARN] Security scanner detected: {', '.join(scanners)}")
        if total_reqs > 0:
            err_pct = (errors_4xx + errors_5xx) / total_reqs * 100
            if err_pct > 30:
                findings.append(f"[WARN] High error rate: {err_pct:.1f}% ({errors_4xx} 4xx, {errors_5xx} 5xx)")

        summary = [f"**Log Analysis** — {len(lines)} lines, {len(ips)} unique IPs\n"]
        if timestamps:
            summary.append(f"  Time range  : {timestamps[0]} → {timestamps[-1]}")
        if ips:
            summary.append(f"  Source IPs  : {', '.join(ips[:10])}" +
                            (f" … +{len(ips)-10}" if len(ips) > 10 else ""))
        if status_counts:
            top = sorted(status_counts.items(), key=lambda x: -x[1])[:5]
            summary.append(f"  HTTP Status : {', '.join(f'{k}×{v}' for k,v in top)}")

        if findings:
            summary.append("\n  **Findings:**")
            summary.extend(f"  {f}" for f in findings)
        else:
            summary.append("\n  ✓ No anomalies detected")

        return SkillResult(True, "\n".join(summary))


def register(registry):
    registry.register("log_analyzer", LogAnalyzerSkill())
