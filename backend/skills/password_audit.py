"""
Skill: password_audit
Category: AUTH
Password policy and authentication security audit.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request

from core.skill_registry import SkillRegistry
from skills._base import Skill

DEEPSEEK_URL   = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

def _call_llm(system: str, messages: list[dict]) -> str:
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY not set")
    payload = json.dumps({
        "model": DEEPSEEK_MODEL, "max_tokens": 256, "temperature": 0.1,
        "messages": [{"role": "system", "content": system}] + messages,
    }).encode()
    req = urllib.request.Request(DEEPSEEK_URL, data=payload, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"]


class PasswordAuditSkill(Skill):
    name             = "password_audit"
    description      = "Password security: policy check, hash detection, lockout probe, compliance"
    usage            = "password_audit <target_url_or_system> [policy_notes]"
    trigger_patterns = ["password audit", "password security", "brute force risk",
                        "auth policy", "password policy"]

    WEAK_PASSWORDS = [
        "password","123456","admin","root","test","letmein","welcome","monkey","dragon","qwerty"
    ]
    HASH_PATTERNS = {
        "MD5":    re.compile(r"^\$1\$"),
        "SHA512": re.compile(r"^\$6\$"),
        "bcrypt": re.compile(r"^\$2[ab]\$"),
        "NTLM":   re.compile(r"^[0-9a-fA-F]{32}$"),
        "SHA256": re.compile(r"^\$5\$"),
    }

    def _detect_hash_algo(self, sample: str) -> str:
        for algo, pat in self.HASH_PATTERNS.items():
            if pat.match(sample.strip()):
                return algo
        return "unknown/plaintext"

    def _probe_lockout(self, url: str) -> dict:
        results = {"lockout_detected": False, "attempts_before_lockout": 0, "detail": ""}
        if not url.startswith(("http://","https://")):
            return results
        i = 0
        for i, pwd in enumerate(self.WEAK_PASSWORDS[:5], 1):
            try:
                data = urllib.parse.urlencode({"username":"admin","password":pwd}).encode()
                req  = urllib.request.Request(url, data=data, method="POST",
                    headers={"Content-Type":"application/x-www-form-urlencoded",
                             "User-Agent":"OMNIKON-SecOps/1.0.2"})
                with urllib.request.urlopen(req, timeout=5) as r:
                    if r.status == 429:
                        results["lockout_detected"] = True
                        results["attempts_before_lockout"] = i
                        results["detail"] = f"HTTP 429 after {i} attempts"
                        break
                    body = r.read(512).decode(errors="replace").lower()
                    if any(x in body for x in ("locked","too many","blocked","captcha")):
                        results["lockout_detected"] = True
                        results["attempts_before_lockout"] = i
                        results["detail"] = f"Lockout keyword in response after {i} attempts"
                        break
            except Exception:
                break
        if not results["lockout_detected"] and i >= 4:
            results["detail"] = f"No lockout after {i} attempts — [CRITICAL] likely no lockout"
        return results

    def run(self, args: str, mm=None) -> SkillResult:
        parts = args.strip().split(None, 1)
        if not parts:
            return SkillResult(False, f"Usage: {self.usage}")
        target       = parts[0]
        policy_notes = parts[1] if len(parts) > 1 else ""
        is_url       = target.startswith(("http://","https://"))

        lines  = [f"**Password Security Audit** `{target}`\n"]
        issues : list[str] = []

        if policy_notes:
            try:
                analysis = _call_llm(
                    "You are a security auditor. Analyse the password policy. "
                    "Respond with ONLY a JSON object: "
                    '{"min_length_ok": true/false, "complexity_ok": true/false, '
                    '"mfa_mentioned": true/false, "lockout_mentioned": true/false, '
                    '"issues": ["issue1", "issue2"]}',
                    [{"role":"user","content":f"Policy: {policy_notes}"}],
                )
                json_m = re.search(r"\{[\s\S]+\}", analysis)
                if json_m:
                    pa = json.loads(json_m.group())
                    lines.append("  **Policy Analysis:**")
                    lines.append(f"    Min length OK : {'✓' if pa.get('min_length_ok') else '✗ [WARN]'}")
                    lines.append(f"    Complexity OK : {'✓' if pa.get('complexity_ok') else '✗ [WARN]'}")
                    lines.append(f"    MFA mentioned : {'✓' if pa.get('mfa_mentioned') else '✗ [WARN]'}")
                    lines.append(f"    Lockout policy: {'✓' if pa.get('lockout_mentioned') else '✗ [WARN]'}")
                    for issue in pa.get("issues", []):
                        issues.append(f"[MEDIUM] Policy: {issue}")
            except Exception:
                lines.append(f"  Policy notes: {policy_notes}")

        if re.match(r"^[\$a-fA-F0-9]", target) and len(target) in (32,40,60,128,106):
            algo = self._detect_hash_algo(target)
            weak = algo in ("MD5","NTLM")
            tag  = "[CRITICAL]" if weak else "[INFO]"
            lines.append(f"  Hash Algorithm: {tag} {algo}")
            if weak:
                issues.append(f"[CRITICAL] Weak hash algorithm: {algo} (crackable)")

        if is_url:
            lockout = self._probe_lockout(target)
            if lockout["lockout_detected"]:
                lines.append(f"  Lockout Probe : ✓ Detected after {lockout['attempts_before_lockout']} attempt(s)")
            else:
                lines.append("  Lockout Probe : [CRITICAL] No account lockout detected — brute-force risk")
                issues.append("[CRITICAL] No lockout mechanism — brute-force feasible")

        if is_url and target.startswith("https://"):
            lines.append("  Transport     : ✓ HTTPS in use")
        elif is_url:
            issues.append("[CRITICAL] HTTP (not HTTPS) — credentials sent in plaintext")
            lines.append("  Transport     : [CRITICAL] HTTP — credentials exposed in transit")

        attack_vectors = [
            ("Credential Stuffing", "high",   "Use of previously breached username/password combos"),
            ("Brute Force",         "high",   "Automated guessing of weak/short passwords"),
            ("Password Spraying",   "medium", "Low-rate guessing of common passwords across accounts"),
        ]

        risk = ("critical" if any("[CRITICAL]" in i for i in issues) else
                "high"     if any("[HIGH]"     in i for i in issues) else
                "medium"   if issues else "low")

        lines.extend([
            "",
            f"  **Risk Level : [{risk.upper()}]**",
            "",
            "  **Attack Vectors:**",
        ])
        for name, sev, desc in attack_vectors:
            lines.append(f"    [{sev.upper():<8}] {name:<22} — {desc}")

        if issues:
            lines.extend(["", "  **Issues:**"])
            lines.extend(f"    {i}" for i in issues)

        return SkillResult(True, "\n".join(lines))


def register(registry):
    registry.register("password_audit", PasswordAuditSkill())
