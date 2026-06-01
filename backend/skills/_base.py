"""
Shared base classes and helpers for all SecOps skills.
Import this in every skill plugin.
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.skill_registry import SkillRegistry, SkillResult


# ─────────────────────────────────────────────────────────────────────────────
# Skill base class
# ─────────────────────────────────────────────────────────────────────────────

class Skill(ABC):
    trigger_patterns: list[str] = []

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def usage(self) -> str: ...

    @property
    def actions(self) -> list[str]:
        return ["run"]

    @abstractmethod
    def run(self, args: str, mm=None) -> SkillResult: ...

    async def execute(self, action: str, params: dict, confirmed: bool = False) -> SkillResult:
        """Adapter so this Skill works with SkillRegistry.execute()"""
        args = params.get("args", "")
        return self.run(args, mm=params.get("mm"))


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_ip(s: str) -> bool:
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def _well_known_service(port: int) -> str:
    SERVICES = {
        21: "ftp", 22: "ssh", 23: "telnet", 25: "smtp", 53: "dns",
        80: "http", 110: "pop3", 143: "imap", 389: "ldap", 443: "https",
        445: "smb", 3306: "mysql", 3389: "rdp", 5432: "postgres",
        6379: "redis", 8080: "http-alt", 8443: "https-alt",
        27017: "mongodb", 5900: "vnc", 11211: "memcached",
    }
    return SERVICES.get(port, "unknown")
