# version: 2.0.0
# changelog:
#   1.0.0 - 2026-05-29 - Initial CBD skill (v1 methodology)
#   2.0.0 - 2026-05-29 - Full rewrite to CBD v2.2: Experienced system, No-Assumption rule,
#                        Version Integrity, Phase -1 clarification gate, all 7 SKL skills,
#                        Phase 0 lookup, Phase III capture. New actions: experienced_lookup,
#                        experienced_capture, experienced_promote, experienced_search,
#                        validate_blueprint, version_read, get_skills_registry.

"""
SKILL: CBDArchitect v2.2
CBD Identity:
  Name: CBDArchitectSkill
  Reason: Full CBD v2.2 enforcement — Phase -1 clarification, Phase 0 Experienced lookup,
          Phase I blueprint with version integrity, Phase II atomic implementation,
          Phase III knowledge capture. Never assumes. Never skips.
  Logical Function: Architecture, Planning, Governance, Knowledge Management

IN-Schema:  { action, ...action-specific params }
OUT-Schema: { success, output: { phase, ... } }

Experience Hook:
  consumer: true  (Phase 0 lookup before every debug/setup action)
  producer: true  (Phase III capture on novel resolution)
  lookup_trigger: always
  write_trigger: on_novel_resolution
"""
import json
import logging
import os
from pathlib import Path
from core.skill_registry import SkillResult

logger = logging.getLogger("skill.cbd")

# ── Experienced system base path (configurable via env var) ───────────────────
_DEFAULT_EXP_PATH = os.getenv("EXPERIENCED_PATH", "./experienced")


def _get_orchestrator(base_path: str = None):
    """Lazy-load orchestrator. Non-blocking if experienced dir missing."""
    try:
        from skills.experienced import AgentLookupOrchestrator
        path = base_path or _DEFAULT_EXP_PATH
        return AgentLookupOrchestrator(path)
    except Exception as e:
        logger.warning(f"Could not init AgentLookupOrchestrator: {e}")
        return None


# ── CBD v2.2 Agent System Prompt ──────────────────────────────────────────────
CBD_SYSTEM_PROMPT = """You are a strict CBD v2.2 Architect Agent operating under the following non-negotiable rules.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PHASE STATE MACHINE — NEVER SKIP A PHASE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PHASE -1 │ REQUIREMENT CLARIFICATION (MANDATORY FIRST)
  • Zero-assumption policy. Any ambiguous or missing requirement → ASK before proceeding.
  • Output a numbered BLOCKING / NON-BLOCKING clarification list.
  • Gate: CLOSED only when user has answered every BLOCKING item.
  • Forbidden: "I'll proceed and adjust later."

PHASE 0  │ EXPERIENCED LOOKUP (MANDATORY BEFORE ANY DEBUG/SETUP)
  • Call ExperienceSearcher on the symptom/error before any fix attempt.
  • If hit → apply known solution and cite the EXP-ID.
  • If miss → proceed and flag for Phase III capture.
  • Audit log: emit LOOKUP_HIT or LOOKUP_MISS.

PHASE I  │ BLUEPRINT & VALIDATION (ARCHITECT MODE — NO CODE)
  • Produce blueprint.md and blueprint.json from version 1.0.0.
  • Component table: Name | Function | IN-Schema | OUT-Schema | Adaptor | Trace Points | Experience Hook
  • Every component needs: Identity, IN, OUT, Error, Trace, Failure Map, Description, Experience Hook.
  • Technical Risk Assessment mandatory: Bottlenecks, Integration Friction, Stability Warnings,
    Experience Gaps, Feasibility Verdict (GO / CAUTION).
  • Version declaration required before any blueprint write:
    "Current blueprint_version is X.Y.Z. This change is [type] → incrementing to A.B.C."
  • Gate: "This is the proposed architecture. Which specific component should I implement first?"

PHASE II │ ATOMIC IMPLEMENTATION (ONE COMPONENT AT A TIME)
  • State: "Current version of [file] is X.Y.Z. This change is [type] → incrementing to A.B.C."
  • Restate IN/OUT schema and trace points for the component being implemented.
  • Every failure point MUST have try-catch. No exceptions.
  • Every file carries a version header and changelog comment.
  • Gate: "Component [X] complete. Approve to continue to next component?"

PHASE III│ EXPERIENCE CAPTURE
  • Any novel defect or environment issue resolved in Phase II → MUST produce an Experienced entry.
  • Use all five blocks: Identity, Discovery, Root Cause, Solution, Prevention.
  • EntryValidator gates CONFIRMED status — DRAFT stays open until all blocks pass.
  • IndexManager.add() after every CONFIRMED entry.
  • Task is NOT closed until the entry reaches CONFIRMED.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CBD COMPONENT CONTRACT (8 ELEMENTS — ALL REQUIRED)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Identity (Name, Reason, Logical Function)
2. IN-Schema
3. OUT-Schema
4. Error-Schema
5. Trace Log Definition
6. Failure Map (try-catch locations)
7. Description
8. Experience Hook (consumer/producer/trigger)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
7 MANDATORY SKILLS (declare gap in Phase -1 if missing)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SKL-001 File System Operations
SKL-002 Blueprint Read & Write
SKL-003 Code Execution & Runtime
SKL-004 Test Creation & Reporting
SKL-005 Security Validation
SKL-006 Documentation & Design Writing
SKL-007 Log Reading & Debug Investigation
"""


class CBDArchitectSkill:
    description = (
        "CBD v2.2 methodology enforcer: Phase -1 clarification gate, Phase 0 Experienced lookup, "
        "Phase I blueprint with version integrity, Phase II atomic implementation, "
        "Phase III knowledge capture. Never assumes. Never skips."
    )
    actions = [
        # ── Core CBD phases ──────────────────────────────────────────────────
        "analyze_request",          # Phase -1: detect ambiguities, list BLOCKING items
        "generate_blueprint",       # Phase I: produce blueprint.md + blueprint.json
        "implement_component",      # Phase II: implement one named component
        "validate_component",       # Check one component against full CBD v2.2 contract
        "validate_blueprint",       # Check entire blueprint JSON for completeness
        # ── Version integrity ────────────────────────────────────────────────
        "version_read",             # Read version from a file header (pre-write protocol)
        # ── Experienced system ───────────────────────────────────────────────
        "experienced_lookup",       # Phase 0: search before any debug/setup
        "experienced_capture",      # Phase III: write + validate + confirm entry
        "experienced_promote",      # Transition entry status (DRAFT→CONFIRMED etc.)
        "experienced_search",       # Ad-hoc search of experience index
        "experienced_rebuild_index",# Rebuild index.md from all EXP-*.md on disk
        # ── Utilities ────────────────────────────────────────────────────────
        "get_template",             # Blank CBD v2.2 blueprint JSON
        "get_skills_registry",      # Return the 7 SKL definitions
    ]

    async def execute(self, action: str, params: dict, confirmed: bool = False) -> SkillResult:
        try:
            # ── Core phases ──────────────────────────────────────────────────
            if action == "analyze_request":
                return self._analyze_request(params)
            elif action == "generate_blueprint":
                return self._generate_blueprint(params)
            elif action == "implement_component":
                return self._implement_component(params)
            elif action == "validate_component":
                return self._validate_component(params)
            elif action == "validate_blueprint":
                return self._validate_blueprint(params)
            # ── Version integrity ─────────────────────────────────────────────
            elif action == "version_read":
                return self._version_read(params)
            # ── Experienced system ────────────────────────────────────────────
            elif action == "experienced_lookup":
                return await self._experienced_lookup(params)
            elif action == "experienced_capture":
                return await self._experienced_capture(params)
            elif action == "experienced_promote":
                return await self._experienced_promote(params)
            elif action == "experienced_search":
                return await self._experienced_search(params)
            elif action == "experienced_rebuild_index":
                return await self._experienced_rebuild_index(params)
            # ── Utilities ─────────────────────────────────────────────────────
            elif action == "get_template":
                return self._get_template()
            elif action == "get_skills_registry":
                return self._get_skills_registry()
            else:
                return SkillResult(False, None, error=f"Unknown CBD action: '{action}'. Valid: {self.actions}")
        except Exception as e:
            logger.error(f"CBDArchitectSkill.{action} error: {e}", exc_info=True)
            return SkillResult(False, None, error=f"{type(e).__name__}: {str(e)}")

    # ── Phase -1 ──────────────────────────────────────────────────────────────

    def _analyze_request(self, params: dict) -> SkillResult:
        request = params.get("request", "")
        if not request.strip():
            return SkillResult(False, None, error="'request' param is required.")
        return SkillResult(True, {
            "phase": "PHASE_MINUS1_CLARIFICATION",
            "system_prompt": CBD_SYSTEM_PROMPT,
            "instruction": (
                "PHASE -1 — REQUIREMENT CLARIFICATION\n\n"
                "Scan the request below. Apply the zero-assumption policy.\n"
                "For every missing, ambiguous, or multi-interpretable requirement:\n"
                "  • Mark BLOCKING if it must be answered before the blueprint can be drawn.\n"
                "  • Mark NON-BLOCKING if it can be defaulted but the default should be stated.\n"
                "Output a numbered clarification list. Then stop — do NOT begin the blueprint.\n"
                "Only close Phase -1 when the user has answered all BLOCKING items.\n\n"
                f"Request:\n{request}"
            ),
            "gate": "GATE: Phase -1 is OPEN. No blueprint until all BLOCKING items are answered.",
        })

    # ── Phase I ───────────────────────────────────────────────────────────────

    def _generate_blueprint(self, params: dict) -> SkillResult:
        request = params.get("request", "")
        clarifications = params.get("clarifications", "")
        context = f"{request}\n\nClarifications answered:\n{clarifications}" if clarifications else request
        return SkillResult(True, {
            "phase": "PHASE_I_BLUEPRINT",
            "system_prompt": CBD_SYSTEM_PROMPT,
            "instruction": (
                "PHASE I — BLUEPRINT GENERATION\n\n"
                "All BLOCKING items from Phase -1 are answered. Proceed to blueprint.\n\n"
                "Produce in order:\n"
                "1. Component table: Name | Logical Function | IN-Schema | OUT-Schema | Adaptor | Trace Points | Experience Hook\n"
                "2. blueprint.json (version 1.0.0, with blueprint_changelog, experienced_system block)\n"
                "3. Technical Risk Assessment:\n"
                "   - Critical Bottlenecks\n"
                "   - Integration Friction\n"
                "   - Stability Warnings\n"
                "   - Experience Gaps (areas with no prior Experienced entries)\n"
                "   - Feasibility Verdict: GO or CAUTION\n\n"
                "Version declaration: State 'blueprint_version starts at 1.0.0'\n\n"
                "End with: 'This is the proposed architecture. Which specific component should I implement first?'\n\n"
                f"Request:\n{context}"
            ),
            "gate": "GATE: No implementation until user explicitly approves blueprint and names first component.",
        })

    # ── Phase II ──────────────────────────────────────────────────────────────

    def _implement_component(self, params: dict) -> SkillResult:
        component_name = params.get("component_name", "")
        blueprint = params.get("blueprint", "")
        language = params.get("language", "Python")
        file_path = params.get("file_path", "")
        current_version = params.get("current_version", "")

        if not component_name:
            return SkillResult(False, None, error="'component_name' is required for Phase II.")

        version_note = (
            f"Current version of the target file is {current_version}. "
            f"Determine increment type, then state the new version before writing."
            if current_version else
            "This is a new file — version starts at 1.0.0."
        )

        return SkillResult(True, {
            "phase": "PHASE_II_IMPLEMENTATION",
            "system_prompt": CBD_SYSTEM_PROMPT,
            "instruction": (
                f"PHASE II — IMPLEMENT COMPONENT: {component_name}\n\n"
                f"Language: {language}\n"
                f"File: {file_path or '(derive from blueprint)'}\n"
                f"Version: {version_note}\n\n"
                "Steps:\n"
                "1. State: 'Current version of [file] is X.Y.Z. This change → incrementing to A.B.C.'\n"
                f"2. Restate the IN-Schema, OUT-Schema, and Trace Points for {component_name}.\n"
                "3. Implement with a try-catch wrapping every identified failure point.\n"
                "4. Add version header + changelog comment at top of file.\n"
                "5. After implementation, state which artifact entry in blueprint.json must be updated.\n\n"
                "End with: 'Component [X] is complete. Approve to continue to next component?'\n\n"
                f"Blueprint context:\n{blueprint}"
            ),
            "gate": f"GATE: Implement ONLY '{component_name}'. Stop after gate question.",
        })

    # ── Validation ────────────────────────────────────────────────────────────

    def _validate_component(self, params: dict) -> SkillResult:
        component = params.get("component", {})
        errors = []
        warnings = []

        for k in ["identity", "interface", "observability", "experience_hook"]:
            if k not in component:
                (errors if k != "experience_hook" else warnings).append(
                    f"Missing key: '{k}'" + (" (required in CBD v2.2)" if k == "experience_hook" else "")
                )

        if "identity" in component:
            for f in ["name", "reason", "logical_function"]:
                if not component["identity"].get(f):
                    errors.append(f"identity.{f} missing or empty")

        if "interface" in component:
            for f in ["in_schema", "out_schema", "error_schema"]:
                if not component["interface"].get(f):
                    warnings.append(f"interface.{f} not defined")

        if "observability" in component:
            obs = component["observability"]
            if not obs.get("trace_points"):
                warnings.append("observability.trace_points empty — at least one required")
            if not obs.get("failure_map"):
                warnings.append("observability.failure_map empty — identify try-catch locations")

        if "experience_hook" in component:
            hook = component["experience_hook"]
            for f in ["consumer", "producer", "lookup_trigger", "write_trigger"]:
                if f not in hook:
                    warnings.append(f"experience_hook.{f} missing (CBD v2.2 required)")

        # Check artifact versioning
        for art in component.get("artifacts", []):
            if not art.get("version"):
                warnings.append(f"artifact '{art.get('file_path','?')}' missing version field (CBD v2.2)")
            if not art.get("changelog"):
                warnings.append(f"artifact '{art.get('file_path','?')}' missing changelog (CBD v2.2)")

        verdict = "PASS" if not errors else "FAIL"
        return SkillResult(True, {
            "verdict": verdict,
            "errors": errors,
            "warnings": warnings,
            "component_name": component.get("identity", {}).get("name", "unknown"),
            "cbd_version": "2.2",
        })

    def _validate_blueprint(self, params: dict) -> SkillResult:
        blueprint = params.get("blueprint", {})
        if isinstance(blueprint, str):
            try:
                blueprint = json.loads(blueprint)
            except json.JSONDecodeError as e:
                return SkillResult(False, None, error=f"Invalid JSON: {e}")

        errors = []
        warnings = []

        # Root-level required fields
        for f in ["project_id", "methodology", "blueprint_version", "blueprint_changelog", "components"]:
            if f not in blueprint:
                errors.append(f"Missing root field: '{f}'")

        # Experienced system block
        if "experienced_system" not in blueprint:
            warnings.append("Missing 'experienced_system' block (required in CBD v2.2)")
        else:
            exp = blueprint["experienced_system"]
            for f in ["enabled", "index_path", "entries_path"]:
                if f not in exp:
                    warnings.append(f"experienced_system.{f} missing")

        # Validate each component
        component_names = []
        for i, comp in enumerate(blueprint.get("components", [])):
            name = comp.get("identity", {}).get("name", f"component[{i}]")
            component_names.append(name)
            result = self._validate_component({"component": comp})
            for err in result.output.get("errors", []):
                errors.append(f"[{name}] {err}")
            for warn in result.output.get("warnings", []):
                warnings.append(f"[{name}] {warn}")

        return SkillResult(True, {
            "verdict": "PASS" if not errors else "FAIL",
            "errors": errors,
            "warnings": warnings,
            "component_count": len(component_names),
            "components_checked": component_names,
            "blueprint_version": blueprint.get("blueprint_version", "unknown"),
            "cbd_version": "2.2",
        })

    # ── Version Integrity ─────────────────────────────────────────────────────

    def _version_read(self, params: dict) -> SkillResult:
        file_path = params.get("file_path", "")
        if not file_path:
            return SkillResult(False, None, error="'file_path' is required.")
        try:
            p = Path(file_path)
            if not p.exists():
                return SkillResult(True, {
                    "file_path": file_path,
                    "version": None,
                    "exists": False,
                    "note": "File does not exist — new file starts at version 1.0.0.",
                })
            content = p.read_text(encoding="utf-8", errors="replace")
            version = None
            changelog = []
            for line in content.splitlines()[:15]:  # version header is always near top
                stripped = line.strip().lstrip("#").lstrip("/").strip()
                if stripped.startswith("version:"):
                    version = stripped.split(":", 1)[1].strip()
                if stripped.startswith("changelog:") or (version and stripped.startswith(version[:3])):
                    changelog.append(stripped)
            return SkillResult(True, {
                "file_path": file_path,
                "version": version,
                "exists": True,
                "changelog_lines": changelog[:5],
                "declaration": (
                    f"Current version of `{p.name}` is {version}."
                    if version else
                    f"`{p.name}` has no version header — add one before proceeding."
                ),
            })
        except Exception as e:
            return SkillResult(False, None, error=f"version_read error: {e}")

    # ── Experienced system actions ────────────────────────────────────────────

    async def _experienced_lookup(self, params: dict) -> SkillResult:
        """Phase 0: mandatory pre-task lookup."""
        task_context = params.get("task_context", {})
        if not task_context.get("symptom_observed") and not task_context.get("error_message"):
            return SkillResult(False, None,
                error="task_context must contain at least 'symptom_observed' or 'error_message'.")
        base_path = params.get("base_path", _DEFAULT_EXP_PATH)
        orch = _get_orchestrator(base_path)
        if not orch:
            return SkillResult(True, {
                "decision": "CREATE_NEW",
                "action_taken": "Experienced system unavailable (non-blocking). Proceeding without lookup.",
                "warning": "ExperienceSearcher could not be initialized.",
            })
        result = orch.lookup(task_context)
        return SkillResult(True, result)

    async def _experienced_capture(self, params: dict) -> SkillResult:
        """Phase III: write, validate, confirm, and index a new experience entry."""
        base_path = params.get("base_path", _DEFAULT_EXP_PATH)
        entry_data = params.get("entry", {})
        if not entry_data:
            return SkillResult(False, None, error="'entry' param is required (ExperienceEntry fields).")
        try:
            from skills.experienced import (
                ExperienceEntry, ExperienceEnvironment, ExperienceDiscovery,
                ExperienceRootCause, ExperienceSolution, ExperiencePrevention,
            )
            env_d = entry_data.get("environment", {})
            entry = ExperienceEntry(
                exp_id=entry_data.get("exp_id"),
                title=entry_data.get("title", ""),
                category=entry_data.get("category", ""),
                severity=entry_data.get("severity", ""),
                date_encountered=entry_data.get("date_encountered", ""),
                environment=ExperienceEnvironment(
                    os=env_d.get("os", ""),
                    runtime_version=env_d.get("runtime_version", ""),
                    framework_version=env_d.get("framework_version", ""),
                    tool_versions=env_d.get("tool_versions", []),
                ),
                discovery=ExperienceDiscovery(**entry_data.get("discovery", {})),
                root_cause=ExperienceRootCause(**entry_data.get("root_cause", {})),
                solution=ExperienceSolution(**entry_data.get("solution", {})),
                prevention=ExperiencePrevention(**entry_data.get("prevention", {})),
                status=entry_data.get("status", "DRAFT"),
            )
        except Exception as e:
            return SkillResult(False, None, error=f"Could not build ExperienceEntry: {e}")

        orch = _get_orchestrator(base_path)
        if not orch:
            return SkillResult(False, None, error="Experienced system unavailable.")
        result = orch.capture(entry)
        success = "error_code" not in result
        return SkillResult(success, result, error=result.get("message") if not success else None)

    async def _experienced_promote(self, params: dict) -> SkillResult:
        """Transition an entry to a new lifecycle status."""
        exp_id = params.get("exp_id", "")
        new_status = params.get("new_status", "")
        superseded_by = params.get("superseded_by")
        base_path = params.get("base_path", _DEFAULT_EXP_PATH)
        if not exp_id or not new_status:
            return SkillResult(False, None, error="'exp_id' and 'new_status' are required.")
        orch = _get_orchestrator(base_path)
        if not orch:
            return SkillResult(False, None, error="Experienced system unavailable.")
        result = orch.promote(exp_id, new_status, superseded_by)
        success = "error_code" not in result
        return SkillResult(success, result, error=result.get("message") if not success else None)

    async def _experienced_search(self, params: dict) -> SkillResult:
        """Ad-hoc search of the experience index."""
        query = params.get("query", "")
        if not query.strip():
            return SkillResult(False, None, error="'query' is required.")
        base_path = params.get("base_path", _DEFAULT_EXP_PATH)
        try:
            from skills.experienced import ExperienceSearcher
            searcher = ExperienceSearcher(base_path)
            result = searcher.search(
                query,
                filters=params.get("filters"),
                max_results=params.get("max_results", 5),
            )
            success = "error_code" not in result
            return SkillResult(success, result)
        except Exception as e:
            return SkillResult(False, None, error=str(e))

    async def _experienced_rebuild_index(self, params: dict) -> SkillResult:
        """Rebuild index.md from all EXP-*.md files on disk."""
        base_path = params.get("base_path", _DEFAULT_EXP_PATH)
        try:
            from skills.experienced import IndexManager
            im = IndexManager(base_path)
            result = im.execute("rebuild")
            success = result.get("success", False)
            return SkillResult(success, result)
        except Exception as e:
            return SkillResult(False, None, error=str(e))

    # ── Utilities ─────────────────────────────────────────────────────────────

    def _get_template(self) -> SkillResult:
        template = {
            "_version": "1.0.0",
            "_changelog": [
                {"version": "1.0.0", "date": "YYYY-MM-DD", "summary": "Initial blueprint"}
            ],
            "project_id": "your-project-id",
            "methodology": "CBD-Interface-First",
            "blueprint_version": "1.0.0",
            "blueprint_changelog": [
                {"version": "1.0.0", "date": "YYYY-MM-DD", "changed_by": "agent", "summary": "Initial blueprint"}
            ],
            "experienced_system": {
                "enabled": True,
                "index_path": "experienced/index.md",
                "entries_path": "experienced/",
                "mandatory_lookup_before": ["debugging", "environment-setup", "dependency-resolution"]
            },
            "components": [
                {
                    "identity": {
                        "name": "COMPONENT_NAME",
                        "reason": "Clear purpose statement",
                        "logical_function": "Function category",
                        "status": "DRAFT"
                    },
                    "interface": {
                        "api": {"endpoint": "/api/path", "method": "POST",
                                "request_structure": {}, "result_structure": {}},
                        "in_schema": {},
                        "out_schema": {},
                        "error_schema": {
                            "codes": [],
                            "format": {"success": False, "error": "<message>", "code": "<code>"}
                        }
                    },
                    "artifacts": [
                        {
                            "file_path": "path/to/file.py",
                            "description": "Purpose",
                            "type": "implementation",
                            "version": "1.0.0",
                            "changelog": [
                                {"version": "1.0.0", "date": "YYYY-MM-DD", "summary": "Initial implementation"}
                            ]
                        }
                    ],
                    "adaptor_requirements": [],
                    "observability": {
                        "trace_points": ["input_received", "processing_started", "output_emitted"],
                        "failure_map": ["try-catch: external_call", "try-catch: schema_validation"]
                    },
                    "experience_hook": {
                        "consumer": True,
                        "producer": False,
                        "lookup_trigger": "on_failure",
                        "write_trigger": "on_novel_resolution"
                    }
                }
            ]
        }
        return SkillResult(True, {"template": template})

    def _get_skills_registry(self) -> SkillResult:
        return SkillResult(True, {
            "cbd_version": "2.2",
            "skills": [
                {"id": "SKL-001", "name": "File System Operations",
                 "phases": ["Phase -1", "Phase 0", "Phase I", "Phase II", "Phase III"], "critical": True,
                 "description": "Read/write all CBD artifacts: blueprint, experienced entries, logs, source files. Atomic write for critical files."},
                {"id": "SKL-002", "name": "Blueprint Read & Write",
                 "phases": ["Phase I", "Phase II"], "critical": True,
                 "description": "Parse blueprint.json, read/increment version, update component schemas and artifact changelog entries."},
                {"id": "SKL-003", "name": "Code Execution & Runtime",
                 "phases": ["Phase II"], "critical": True,
                 "description": "Run build tools, execute components in isolation, manage Docker containers, capture stdout/stderr."},
                {"id": "SKL-004", "name": "Test Creation & Reporting",
                 "phases": ["Phase II", "Phase III"], "critical": True,
                 "description": "Write unit/negative/boundary tests per IN/OUT contract. No component is COMPLETE without passing tests."},
                {"id": "SKL-005", "name": "Security Validation",
                 "phases": ["Phase I", "Phase II"], "critical": True,
                 "description": "IN-Schema validation enforcement, no hardcoded secrets, dependency scan, auth/authz checks."},
                {"id": "SKL-006", "name": "Documentation & Design Writing",
                 "phases": ["Phase I", "Phase III"], "critical": True,
                 "description": "Solution design doc, component README, SETUP.md. All with version headers and changelog."},
                {"id": "SKL-007", "name": "Log Reading & Debug Investigation",
                 "phases": ["Phase 0", "Phase II"], "critical": True,
                 "description": "Read system.log, llm_interaction.log, experience.log, Docker logs. Feed verbatim errors to Phase 0 lookup."},
            ]
        })
