"""
Skill: ioc_extractor
Category: THREAT
Extract all IOCs (IPs, domains, hashes, CVEs, emails, URLs) from text.
"""
from __future__ import annotations

import re

from core.skill_registry import SkillRegistry, SkillResult
from skills._base import Skill


class IocExtractorSkill(Skill):
    name             = "ioc_extractor"
    description      = "Extract IOCs from text: IPs, domains, hashes, CVEs, emails, URLs"
    usage            = "ioc_extractor <text>"
    trigger_patterns = ["extract ioc", "find indicators", "ioc extract", "parse indicators"]

    _PATTERNS = {
        "IPv4":   re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"),
        "IPv6":   re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b"),
        "Domain": re.compile(r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+(?:com|net|org|io|gov|edu|co|uk|de|fr|ru|cn|info|biz|onion)\b"),
        "URL":    re.compile(r"https?://[^\s\"'<>]{8,200}"),
        "Email":  re.compile(r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b"),
        "MD5":    re.compile(r"\b[0-9a-fA-F]{32}\b"),
        "SHA1":   re.compile(r"\b[0-9a-fA-F]{40}\b"),
        "SHA256": re.compile(r"\b[0-9a-fA-F]{64}\b"),
        "CVE":    re.compile(r"\bCVE-\d{4}-\d{4,}\b", re.I),
    }

    def run(self, args: str, mm=None) -> SkillResult:
        if not args.strip():
            return SkillResult(False, f"Usage: {self.usage}")

        results: dict[str, list[str]] = {}
        for ioc_type, pattern in self._PATTERNS.items():
            found = sorted(set(pattern.findall(args)))
            if found:
                results[ioc_type] = found

        if not results:
            return SkillResult(True, "No IOCs found in provided text.")

        total = sum(len(v) for v in results.values())
        lines = [f"**IOC Extraction** — {total} indicators found\n"]
        for ioc_type, items in results.items():
            lines.append(f"  {ioc_type} ({len(items)}):")
            for item in items[:20]:
                lines.append(f"    {item}")
            if len(items) > 20:
                lines.append(f"    … and {len(items) - 20} more")

        return SkillResult(True, "\n".join(lines))


def register(registry):
    registry.register("ioc_extractor", IocExtractorSkill())
