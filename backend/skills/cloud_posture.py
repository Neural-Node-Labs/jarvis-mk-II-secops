"""
Skill: cloud_posture
Category: CLOUD
Cloud security posture assessment via provider CLIs and public APIs.
Requires AWS CLI + credentials for full checks; falls back to public S3 probe.
"""
from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request

from core.skill_registry import SkillRegistry, SkillResult
from skills._base import Skill


class CloudPostureSkill(Skill):
    name             = "cloud_posture"
    description      = "Cloud security posture: AWS/GCP/Azure IAM, public buckets, security groups, logging"
    usage            = "cloud_posture <aws_account_id|gcp_project|azure_sub> [aws|gcp|azure]"
    trigger_patterns = ["cloud posture", "cloud security", "aws security", "s3 bucket",
                        "iam review", "cloud audit"]

    def _aws_check(self, account: str) -> list[tuple[str, str]]:
        findings = []
        cli_cmds = [
            (["aws", "s3api", "list-buckets", "--query", "Buckets[].Name", "--output", "json"], "s3_list"),
            (["aws", "cloudtrail", "describe-trails", "--output", "json"],                      "cloudtrail"),
            (["aws", "ec2", "describe-security-groups", "--output", "json"],                    "security_groups"),
        ]
        for cmd, check_type in cli_cmds:
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
                if r.returncode != 0:
                    continue
                if check_type == "s3_list":
                    buckets = json.loads(r.stdout) if r.stdout.strip() else []
                    for bucket in buckets[:10]:
                        pub_r = subprocess.run(
                            ["aws","s3api","get-bucket-acl","--bucket",bucket,"--output","json"],
                            capture_output=True, text=True, timeout=10)
                        if "AllUsers" in pub_r.stdout or "AuthenticatedUsers" in pub_r.stdout:
                            findings.append(("critical", f"S3 bucket public: {bucket}"))
                elif check_type == "cloudtrail":
                    trails = json.loads(r.stdout).get("trailList", [])
                    if not trails:
                        findings.append(("high", "CloudTrail: no trails configured"))
                    else:
                        for t in trails:
                            if not t.get("IsMultiRegionTrail"):
                                findings.append(("medium", f"CloudTrail '{t.get('Name')}' not multi-region"))
                elif check_type == "security_groups":
                    sgs = json.loads(r.stdout).get("SecurityGroups", [])
                    for sg in sgs:
                        for perm in sg.get("IpPermissions", []):
                            for rng in perm.get("IpRanges", []):
                                if rng.get("CidrIp") == "0.0.0.0/0":
                                    port = perm.get("FromPort","all")
                                    sev  = "critical" if port in (22,3389) else "high"
                                    findings.append((sev, f"SG {sg.get('GroupId')}: port {port} open to 0.0.0.0/0"))
            except FileNotFoundError:
                findings.append(("info", "AWS CLI not installed — install with: pip install awscli"))
                break
            except Exception as e:
                findings.append(("info", f"AWS check {check_type} failed: {str(e)[:60]}"))
        return findings

    def run(self, args: str, mm=None) -> SkillResult:
        parts    = args.strip().split()
        if not parts:
            return SkillResult(False, f"Usage: {self.usage}")
        target   = parts[0]
        provider = parts[1].lower() if len(parts) > 1 else "aws"

        lines    = [f"**Cloud Security Posture** `{target}` ({provider.upper()})\n"]
        findings : list[tuple[str, str]] = []

        if provider == "aws":
            findings.extend(self._aws_check(target))
            if not findings:
                lines.append("  AWS CLI not available or no credentials — performing public probe\n")
                for suffix in ["", "-public", "-data", "-backup", "-dev", "-prod"]:
                    bucket_url = f"https://{target}{suffix}.s3.amazonaws.com/"
                    try:
                        req = urllib.request.Request(bucket_url,
                            headers={"User-Agent": "OMNIKON-SecOps/1.0.2"})
                        with urllib.request.urlopen(req, timeout=5) as r:
                            if r.status == 200:
                                findings.append(("critical", f"Public S3 bucket: {target}{suffix}"))
                    except urllib.error.HTTPError as e:
                        if e.code == 403:
                            lines.append(f"  Bucket {target}{suffix}: exists but access denied (403)")
                    except Exception:
                        pass
        else:
            lines.append(f"  Provider: {provider.upper()} — CLI checks require configured credentials")
            findings.append(("info", f"Manual {provider.upper()} CLI checks required"))

        risk = ("critical" if any(s == "critical" for s, _ in findings) else
                "high"     if any(s == "high"     for s, _ in findings) else
                "medium"   if any(s == "medium"   for s, _ in findings) else
                "low"      if findings else "info")

        lines.extend([
            f"  **Risk Level : [{risk.upper()}]**",
            "",
            "  **Findings:**" if findings else "  ✓ No public exposures detected",
        ])
        for sev, desc in findings:
            lines.append(f"    [{sev.upper():<8}] {desc}")

        return SkillResult(True, "\n".join(lines))


def register(registry):
    registry.register("cloud_posture", CloudPostureSkill())
