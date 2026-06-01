"""
Skill: hash_lookup
Category: THREAT
Compute file/string hash AND query VirusTotal API if key is set.
Requires VIRUSTOTAL_API_KEY env var for live lookups (optional).
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from core.skill_registry import SkillRegistry
from skills._base import Skill


class HashLookupSkill(Skill):
    name             = "hash_lookup"
    description      = "Hash a string/file (MD5/SHA1/SHA256) and optionally query VirusTotal"
    usage            = "hash_lookup <text_or_filepath> [md5|sha1|sha256]"
    trigger_patterns = ["hash lookup", "virustotal", "file hash", "malware hash", "check hash"]
    VT_URL           = "https://www.virustotal.com/api/v3/files/"

    def _hash_data(self, data: bytes) -> dict[str, str]:
        return {
            "md5":    hashlib.md5(data).hexdigest(),
            "sha1":   hashlib.sha1(data).hexdigest(),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def _vt_lookup(self, sha256: str) -> dict[str, Any] | None:
        api_key = os.getenv("VIRUSTOTAL_API_KEY", "")
        if not api_key:
            return None
        try:
            req = urllib.request.Request(
                f"{self.VT_URL}{sha256}",
                headers={"x-apikey": api_key, "User-Agent": "OMNIKON-SecOps/1.0.2"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {"not_found": True}
            return None
        except Exception:
            return None

    def run(self, args: str, mm=None) -> SkillResult:
        parts = args.strip().split()
        if not parts:
            return SkillResult(False, f"Usage: {self.usage}")

        target   = parts[0]
        req_algo = parts[1].lower() if len(parts) > 1 else "sha256"

        p = Path(target)
        if p.exists() and p.is_file():
            data = p.read_bytes()
            src  = f"file:{target}"
        else:
            data = target.encode()
            src  = f"string:{target[:50]}"

        hashes = self._hash_data(data)
        lines  = [
            f"**Hash Lookup** ({src})\n",
            f"  MD5    : {hashes['md5']}",
            f"  SHA1   : {hashes['sha1']}",
            f"  SHA256 : {hashes['sha256']}",
        ]

        vt = self._vt_lookup(hashes["sha256"])
        if vt is None:
            lines.append("  VT     : set VIRUSTOTAL_API_KEY for live lookups")
        elif vt.get("not_found"):
            lines.append("  VT     : not found in VirusTotal database")
        else:
            try:
                stats = vt["data"]["attributes"]["last_analysis_stats"]
                mal   = stats.get("malicious", 0)
                total = sum(stats.values())
                tag   = "[CRITICAL]" if mal > 5 else ("[WARN]" if mal > 0 else "[CLEAN]")
                lines.append(f"  VT     : {tag} {mal}/{total} engines flagged malicious")
            except (KeyError, TypeError):
                lines.append("  VT     : response parse error")

        return SkillResult(True, "\n".join(lines))


def register(registry):
    registry.register("hash_lookup", HashLookupSkill())
