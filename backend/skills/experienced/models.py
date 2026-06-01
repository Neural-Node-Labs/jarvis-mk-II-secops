# version: 1.0.0
# changelog:
#   1.0.0 - 2026-05-29 - Initial models for Experienced.md system (CBD v2.2)

"""
Shared data models for the Experienced.md Knowledge System.
All schemas are derived verbatim from juan-experienced-blueprint.md.
"""
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timezone


# ── Valid enumerations ────────────────────────────────────────────────────────

VALID_CATEGORIES = {
    "dependency-conflict", "environment-setup", "configuration",
    "build-tooling", "runtime-crash", "integration",
    "test-infrastructure", "performance-degradation", "security-constraint",
}

VALID_SEVERITIES = {"blocking", "degrading", "confusing"}

VALID_STATUSES = {"DRAFT", "CONFIRMED", "STABLE", "SUPERSEDED"}

# Whitelist of allowed lifecycle transitions — any unlisted pair is rejected
VALID_TRANSITIONS = {
    ("DRAFT",      "CONFIRMED"),
    ("CONFIRMED",  "STABLE"),
    ("CONFIRMED",  "SUPERSEDED"),
    ("STABLE",     "SUPERSEDED"),
}


# ── Core data types ───────────────────────────────────────────────────────────

@dataclass
class ExperienceEnvironment:
    os: str
    runtime_version: str
    framework_version: str
    tool_versions: list = field(default_factory=list)


@dataclass
class ExperienceDiscovery:
    symptom: str                    # verbatim — minimum 20 chars enforced by EntryValidator
    discovery_path: str
    misleading_signals: list = field(default_factory=list)


@dataclass
class ExperienceRootCause:
    statement: str                  # single sentence — enforced by EntryValidator
    explanation: str
    trigger_conditions: str


@dataclass
class ExperienceSolution:
    fix: str
    why_it_works: str
    alternatives_considered: list = field(default_factory=list)
    side_effects: str = ""


@dataclass
class ExperiencePrevention:
    detection_hints: list = field(default_factory=list)
    recommended_checks: list = field(default_factory=list)
    related_entries: list = field(default_factory=list)  # EXP-IDs


@dataclass
class ExperienceEntry:
    """Full experience entry — all five blocks required for CONFIRMED status."""
    exp_id: Optional[str]
    title: str
    category: str
    severity: str
    date_encountered: str           # ISO8601 date
    environment: ExperienceEnvironment
    discovery: ExperienceDiscovery
    root_cause: ExperienceRootCause
    solution: ExperienceSolution
    prevention: ExperiencePrevention
    status: str = "DRAFT"


@dataclass
class IndexRecord:
    """Single row in experienced/index.md"""
    exp_id: str
    title: str
    category: str
    severity: str
    status: str
    environment_summary: str
    date_encountered: str
    file_path: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def next_exp_id(existing_ids: list) -> str:
    """Generate the next sequential EXP-XXXX ID."""
    nums = []
    for eid in existing_ids:
        try:
            nums.append(int(eid.replace("EXP-", "")))
        except ValueError:
            pass
    next_num = (max(nums) + 1) if nums else 1
    return f"EXP-{next_num:04d}"
