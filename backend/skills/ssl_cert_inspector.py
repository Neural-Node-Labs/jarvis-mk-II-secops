"""
Skill: ssl_cert_inspector
Category: NETWORK
Real SSL/TLS certificate inspection via stdlib ssl.
"""
from __future__ import annotations

import socket
import ssl
from datetime import datetime, timezone

from core.skill_registry import SkillRegistry, SkillResult
from skills._base import Skill


class SslCertInspectorSkill(Skill):
    name             = "ssl_cert_inspector"
    description      = "Inspect TLS certificate: expiry, issuer, SANs, cipher, protocol"
    usage            = "ssl_cert_inspector <hostname> [port]  (default port 443)"
    trigger_patterns = ["ssl cert", "tls certificate", "certificate expiry", "https cert"]

    def run(self, args: str, mm=None) -> SkillResult:
        parts = args.strip().split()
        if not parts:
            return SkillResult(False, f"Usage: {self.usage}")
        host = parts[0]
        port = int(parts[1]) if len(parts) > 1 else 443

        try:
            ctx = ssl.create_default_context()
            with socket.create_connection((host, port), timeout=10) as raw_sock:
                with ctx.wrap_socket(raw_sock, server_hostname=host) as s:
                    cert    = s.getpeercert()
                    cipher  = s.cipher()
                    version = s.version()

            not_after  = datetime.strptime(cert["notAfter"],  "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
            not_before = datetime.strptime(cert["notBefore"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
            now        = datetime.now(timezone.utc)
            days_left  = (not_after - now).days
            expired    = days_left < 0
            expiry_tag = "[CRITICAL] EXPIRED" if expired else (
                         "[WARN] expires soon" if days_left < 30 else "valid")

            subject = dict(x[0] for x in cert.get("subject", []))
            issuer  = dict(x[0] for x in cert.get("issuer",  []))
            sans    = [v for typ, v in cert.get("subjectAltName", []) if typ == "DNS"]

            lines = [
                f"**SSL Certificate** `{host}:{port}`\n",
                f"  TLS Version : {version}",
                f"  Cipher      : {cipher[0] if cipher else 'unknown'}",
                f"  CN          : {subject.get('commonName', 'N/A')}",
                f"  Issuer      : {issuer.get('organizationName', 'N/A')}",
                f"  Valid From  : {not_before.strftime('%Y-%m-%d')}",
                f"  Expiry      : {not_after.strftime('%Y-%m-%d')} — {days_left}d left [{expiry_tag}]",
                f"  SANs        : {', '.join(sans[:10]) if sans else 'none'}",
            ]
            if len(sans) > 10:
                lines.append(f"  ... and {len(sans) - 10} more SANs")

            return SkillResult(True, "\n".join(lines))

        except ssl.SSLCertVerificationError as e:
            return SkillResult(True,
                f"**SSL Certificate** `{host}:{port}`\n  [CRITICAL] Cert verification failed: {e}")
        except ssl.SSLError as e:
            return SkillResult(False, f"⚠ SSL error: {e}")
        except (socket.timeout, ConnectionRefusedError, OSError) as e:
            return SkillResult(False, f"⚠ Connection failed: {e}")


def register(registry):
    registry.register("ssl_cert_inspector", SslCertInspectorSkill())
