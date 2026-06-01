"""
session.py — v1.0.0
CHANGELOG: Initial EvolutionSession dataclass — holds all state for an evolution cycle
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Dict, Any, List


class PhaseStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    DONE = "done"
    BLOCKED = "blocked"
    FAILED = "failed"


class SessionStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


class LogType(str, Enum):
    INFO = "info"
    SUCCESS = "success"
    WARN = "warn"
    ERROR = "error"
    MUTED = "muted"


@dataclass
class LogEntry:
    timestamp: str
    message: str
    type: str = "info"

    def to_dict(self) -> dict:
        return {"timestamp": self.timestamp, "message": self.message, "type": self.type}


@dataclass
class ExpEntry:
    exp_id: str
    category: str
    title: str
    status: str = "DRAFT"  # DRAFT | CONFIRMED

    def to_dict(self) -> dict:
        return {"id": self.exp_id, "category": self.category, "title": self.title, "status": self.status}


@dataclass
class PhaseState:
    phase_id: int
    label: str
    description: str
    status: PhaseStatus = PhaseStatus.IDLE

    def to_dict(self) -> dict:
        return {
            "id": self.phase_id,
            "label": self.label,
            "description": self.description,
            "status": self.status.value,
        }


# The 10 CBD v2.2 Evolution Phases
PHASES = [
    {"id": 0, "label": "Experience lookup", "description": "Phase 0 — consult experienced/index.md"},
    {"id": 1, "label": "Workspace bootstrap", "description": "Phase 1 — create /tmp/evo/ workspace"},
    {"id": 2, "label": "Source discovery", "description": "Phase 2 — copy files + read versions"},
    {"id": 3, "label": "Code analysis", "description": "Phase 3 — write analysis.md report"},
    {"id": 4, "label": "Blueprint generation", "description": "Phase 4 — blueprint.md + blueprint.json"},
    {"id": 5, "label": "Implementation", "description": "Phase 5 — apply changes, version files"},
    {"id": 6, "label": "Build validation", "description": "Phase 6 — syntax, deps, smoke test"},
    {"id": 7, "label": "UI & API testing", "description": "Phase 7 — generate + run test suite"},
    {"id": 8, "label": "Deploy & verify", "description": "Phase 8 — copy back + hash verify"},
    {"id": 9, "label": "Restart & health", "description": "Phase 9 — restart + health check + EXP"},
]


@dataclass
class EvolutionSession:
    """Mutable session state for a single evolution run."""

    workspace_id: str
    workspace_path: str
    target_skill: str = ""
    task_description: str = ""
    status: SessionStatus = SessionStatus.IDLE
    current_phase: int = -1
    approved: bool = False
    aborted: bool = False

    phases: List[PhaseState] = field(default_factory=list)
    logs: List[LogEntry] = field(default_factory=list)
    exp_entries: List[ExpEntry] = field(default_factory=list)

    stats: Dict[str, str] = field(default_factory=lambda: {
        "workspaceId": "—",
        "filesCopied": "—",
        "testsPassed": "—",
        "expCount": "—",
    })

    verdict: Optional[str] = None  # "pass" | "fail" | None

    def __post_init__(self):
        if not self.phases:
            self.phases = [
                PhaseState(phase_id=p["id"], label=p["label"], description=p["description"])
                for p in PHASES
            ]

    def add_log(self, message: str, log_type: LogType = LogType.INFO) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.logs.append(LogEntry(timestamp=ts, message=message, type=log_type.value))

    def add_exp_entry(self, exp_id: str, category: str, title: str, status: str = "CONFIRMED") -> None:
        self.exp_entries.append(ExpEntry(exp_id=exp_id, category=category, title=title, status=status))
        self.stats["expCount"] = str(len(self.exp_entries))

    def set_phase_status(self, phase_id: int, status: PhaseStatus) -> None:
        for i, p in enumerate(self.phases):
            if i < phase_id and status == PhaseStatus.RUNNING:
                p.status = PhaseStatus.DONE
            elif i == phase_id:
                p.status = status

    def to_dict(self) -> dict:
        return {
            "running": self.status == SessionStatus.RUNNING,
            "currentPhase": self.current_phase,
            "paused": self.status == SessionStatus.AWAITING_APPROVAL,
            "approved": self.approved,
            "aborted": self.aborted,
            "workspaceId": self.stats.get("workspaceId", self.workspace_id),
            "filesCopied": self.stats.get("filesCopied", "—"),
            "testsPassed": self.stats.get("testsPassed", "—"),
            "expCount": self.stats.get("expCount", "—"),
            "verdict": self.verdict,
            "phases": [p.to_dict() for p in self.phases],
            "gateStatus": self._gate_status(),
            "logs": [l.to_dict() for l in self.logs],
            "expEntries": [e.to_dict() for e in self.exp_entries],
        }

    def _gate_status(self) -> str:
        if self.status == SessionStatus.AWAITING_APPROVAL:
            return "waiting"
        if self.status == SessionStatus.APPROVED or self.approved:
            return "approved"
        if self.current_phase >= 5:
            return "approved"  # past the gate
        return "hidden"
