"""
Skill: cve_lookup
Category: THREAT
Real CVE data from NVD (NIST) public API — no key required.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from core.skill_registry import SkillRegistry, SkillResult
from skills._base import Skill


class CveLookupSkill(Skill):
    name             = "cve_lookup"
    description      = "Look up CVE details from NVD/NIST (real API, no key needed)"
    usage            = "cve_lookup <CVE-YYYY-NNNNN>"
    trigger_patterns = ["cve lookup", "vulnerability details", "check cve", "nvd lookup"]
    NVD_URL          = "https://services.nvd.nist.gov/rest/json/cves/2.0"

    def run(self, args: str, mm=None) -> SkillResult:
        cve_id = args.strip().upper()
        if not re.match(r"CVE-\d{4}-\d+", cve_id):
            return SkillResult(False, f"⚠ Invalid format. Usage: {self.usage}")

        url = f"{self.NVD_URL}?cveId={cve_id}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "OMNIKON-SecOps/1.0.2"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read())

            vulns = data.get("vulnerabilities", [])
            if not vulns:
                return SkillResult(True, f"No NVD data found for {cve_id}")

            cve  = vulns[0]["cve"]
            desc = next((d["value"] for d in cve.get("descriptions", [])
                         if d.get("lang") == "en"), "No description")

            metrics    = cve.get("metrics", {})
            cvss_score = "N/A"
            cvss_sev   = "N/A"
            for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
                if key in metrics and metrics[key]:
                    m          = metrics[key][0]["cvssData"]
                    cvss_score = m.get("baseScore", "N/A")
                    cvss_sev   = m.get("baseSeverity", "N/A")
                    break

            published = cve.get("published", "")[:10]
            modified  = cve.get("lastModified", "")[:10]
            refs      = [r["url"] for r in cve.get("references", [])[:3]]

            sev_tag = ""
            try:
                score = float(cvss_score)
                sev_tag = "[CRITICAL]" if score >= 9.0 else (
                          "[HIGH]"     if score >= 7.0 else (
                          "[MEDIUM]"   if score >= 4.0 else "[LOW]"))
            except (ValueError, TypeError):
                pass

            lines = [
                f"**CVE** `{cve_id}` {sev_tag}\n",
                f"  CVSS Score  : {cvss_score} ({cvss_sev})",
                f"  Published   : {published}",
                f"  Last Mod    : {modified}",
                f"  Description : {desc[:400]}",
            ]
            if refs:
                lines.append("  References  :")
                for r in refs:
                    lines.append(f"    {r}")

            return SkillResult(True, "\n".join(lines))

        except urllib.error.URLError as e:
            return SkillResult(False, f"⚠ NVD API error: {e}")
        except Exception as e:
            return SkillResult(False, f"⚠ {e}")


def register(registry):
    registry.register("cve_lookup", CveLookupSkill())
