"""
CBD Architect Skill — CBD v2.2 methodology enforcer.
Implements the full phase pipeline: Phase -1, 0, I, II, III.
Manages blueprints, Experienced system, component versioning.

version: 1.0.0
changelog:
  1.0.0 - 2026-06-10 - Initial. All actions per cbd.md v2.2 and cbd-skills.md.
                        Actions: analyze_request, generate_blueprint, validate_blueprint,
                        implement_component, validate_component, version_read,
                        experienced_lookup, experienced_capture, experienced_promote,
                        experienced_search, experienced_rebuild_index,
                        get_template, get_skills_registry.
"""
import os
import json
import re
import logging
import traceback
from datetime import datetime, timezone
from core.skill_registry import SkillResult

logger = logging.getLogger("skill.cbd_architect")

# ── Paths ──────────────────────────────────────────────────────────────────────
BLUEPRINT_JSON  = os.getenv("BLUEPRINT_JSON",  "./blueprint.json")
BLUEPRINT_MD    = os.getenv("BLUEPRINT_MD",    "./blueprint.md")
EXPERIENCED_DIR = os.getenv("EXPERIENCED_DIR", "./experienced")
EXP_INDEX       = os.path.join(EXPERIENCED_DIR, "index.md")

# Valid lifecycle statuses
EXP_STATUSES    = ["DRAFT", "CONFIRMED", "STABLE", "SUPERSEDED"]
COMPONENT_STATUSES = ["DRAFT", "IN_PROGRESS", "COMPLETE", "DEPRECATED"]


class CbdArchitectSkill:

    async def execute(self, action: str, params: dict, confirmed: bool = False) -> SkillResult:
        dispatch = {
            # Core CBD phases
            "analyze_request":           self._analyze_request,
            "generate_blueprint":        self._generate_blueprint,
            "validate_blueprint":        self._validate_blueprint,
            "implement_component":       self._implement_component,
            "validate_component":        self._validate_component,
            "version_read":              self._version_read,
            # Experienced system
            "experienced_lookup":        self._experienced_lookup,
            "experienced_capture":       self._experienced_capture,
            "experienced_promote":       self._experienced_promote,
            "experienced_search":        self._experienced_search,
            "experienced_rebuild_index": self._experienced_rebuild_index,
            # Meta
            "get_template":              self._get_template,
            "get_skills_registry":       self._get_skills_registry,
        }
        fn = dispatch.get(action)
        if fn is None:
            return SkillResult.fail(
                f"CBD_UNKNOWN_ACTION: '{action}'. Valid: {', '.join(sorted(dispatch))}"
            )
        try:
            return await fn(params, confirmed)
        except Exception as exc:
            logger.error("[cbd.%s] %s\n%s", action, exc, traceback.format_exc())
            return SkillResult.fail(f"CBD_INTERNAL_ERROR: {type(exc).__name__}: {exc}")

    # ═══════════════════════════════════════════════════════════════════════════
    # CBD PHASE ACTIONS
    # ═══════════════════════════════════════════════════════════════════════════

    async def _analyze_request(self, params: dict, confirmed: bool) -> SkillResult:
        """
        Phase -1 / Phase 0: Analyse a request, identify ambiguities, run Experienced lookup.
        IN:  { request: str, context?: str }
        OUT: { clarification_questions[], experienced_hits[], recommendation }
        """
        request = params.get("request", "")
        if not request:
            return SkillResult.fail("CBD_MISSING_PARAM: request is required")

        # Run Experienced lookup first (CBD Principle #5 — Experienced-First)
        exp_hits = await self._do_experienced_search(request, max_results=3)

        # Generate clarification checklist (pattern-based — agent will refine)
        questions = []
        if not any(w in request.lower() for w in ["language", "python", "java", "go", "node", "rust"]):
            questions.append("What is the target language/runtime?")
        if not any(w in request.lower() for w in ["test", "spec", "coverage"]):
            questions.append("What test framework and coverage level is required?")
        if not any(w in request.lower() for w in ["docker", "deploy", "cloud", "container", "local"]):
            questions.append("What is the target deployment environment?")
        if not any(w in request.lower() for w in ["auth", "api key", "jwt", "oauth", "anon", "public"]):
            questions.append("What authentication strategy is required?")

        return SkillResult.ok({
            "request":                  request,
            "phase":                    "-1 (Clarification)",
            "clarification_questions":  questions,
            "ambiguity_count":          len(questions),
            "experienced_hits":         exp_hits,
            "experienced_hit_count":    len(exp_hits),
            "recommendation":           (
                "PROCEED_WITH_BLUEPRINT" if not questions
                else "CLARIFY_FIRST — resolve all clarification questions before generating blueprint"
            ),
        })

    async def _generate_blueprint(self, params: dict, confirmed: bool) -> SkillResult:
        """
        Phase I: Generate blueprint.md and blueprint.json from a confirmed spec.
        IN:  { project_id, components[], description, methodology? }
        OUT: { blueprint_md, blueprint_json, version, written_to_disk }
        """
        project_id  = params.get("project_id", "unnamed-project")
        components  = params.get("components", [])
        description = params.get("description", "")
        methodology = params.get("methodology", "CBD-Interface-First v2.2")

        if not components:
            return SkillResult.fail("CBD_MISSING_PARAM: components[] is required for blueprint generation")

        now     = datetime.now(timezone.utc).isoformat()
        version = "1.0.0"

        # Build blueprint.json
        bp_json = {
            "_version":          version,
            "project_id":        project_id,
            "methodology":       methodology,
            "blueprint_version": version,
            "blueprint_changelog": [{
                "version":    version,
                "date":       now,
                "changed_by": "agent",
                "summary":    "Initial blueprint generated by cbd_architect",
            }],
            "experienced_system": {
                "enabled":                 True,
                "index_path":              EXP_INDEX,
                "entries_path":            EXPERIENCED_DIR,
                "mandatory_lookup_before": ["debugging", "environment-setup", "dependency-resolution"],
            },
            "components": [_default_component_block(c) for c in components],
        }

        # Build blueprint.md header
        comp_table_rows = "\n".join(
            f"| {i+1} | `{c.get('name', 'Unknown')}` | {c.get('function', '—')} | "
            f"{c.get('in_schema', '—')} | {c.get('out_schema', '—')} | None | — | Consumer |"
            for i, c in enumerate(components)
        )
        bp_md = f"""# Blueprint — {project_id}

**Project ID**: `{project_id}`
**Methodology**: {methodology}
**Blueprint Version**: {version}
**Status**: AWAITING APPROVAL
**Date**: {now[:10]}

---

## Purpose

{description or 'Generated by cbd_architect. Add purpose description.'}

---

## Component Table

| # | Component Name | Logical Function | IN Schema | OUT Schema | Adaptor | Trace Points | Exp Hook |
|---|---|---|---|---|---|---|---|
{comp_table_rows}

---

*Blueprint generated by agent on {now[:10]}. Awaiting explicit written approval before Phase II implementation begins.*
"""

        # Write to disk
        written = []
        try:
            os.makedirs(os.path.dirname(os.path.abspath(BLUEPRINT_MD)), exist_ok=True)
            _atomic_write(BLUEPRINT_MD, bp_md)
            _atomic_write(BLUEPRINT_JSON, json.dumps(bp_json, indent=2))
            written = [BLUEPRINT_MD, BLUEPRINT_JSON]
            logger.info("[cbd.generate_blueprint] project=%s version=%s files=%s",
                        project_id, version, written)
        except Exception as exc:
            logger.error("[cbd.generate_blueprint.write_error] %s", exc)

        return SkillResult.ok({
            "project_id":     project_id,
            "version":        version,
            "blueprint_md":   bp_md,
            "blueprint_json": bp_json,
            "written_to_disk": written,
            "next_step":       "Present blueprint to user for EXPLICIT WRITTEN APPROVAL before Phase II",
        })

    async def _validate_blueprint(self, params: dict, confirmed: bool) -> SkillResult:
        """
        Read and validate the current blueprint.json against CBD v2.2 requirements.
        IN:  { path?: str }
        OUT: { valid, version, issues[], component_count }
        """
        path = params.get("path", BLUEPRINT_JSON)
        if not os.path.isfile(path):
            return SkillResult.fail(f"CBD_BLUEPRINT_NOT_FOUND: '{path}'")

        try:
            with open(path, "r") as f:
                bp = json.load(f)
        except json.JSONDecodeError as exc:
            return SkillResult.fail(f"CBD_BLUEPRINT_PARSE_ERROR: {exc}")

        issues = []

        # Required top-level fields
        for field in ("project_id", "methodology", "blueprint_version", "blueprint_changelog", "components"):
            if field not in bp:
                issues.append(f"MISSING_FIELD: '{field}'")

        # Components validation
        for i, comp in enumerate(bp.get("components", [])):
            name = comp.get("identity", {}).get("name", f"component[{i}]")
            if "identity" not in comp:
                issues.append(f"{name}: missing 'identity' block")
            if "interface" not in comp:
                issues.append(f"{name}: missing 'interface' block")
            if "experience_hook" not in comp:
                issues.append(f"{name}: missing 'experience_hook'")

        # Version format check
        ver = bp.get("blueprint_version", "")
        if not re.match(r"^\d+\.\d+\.\d+$", ver):
            issues.append(f"INVALID_VERSION_FORMAT: '{ver}' — must be MAJOR.MINOR.PATCH")

        return SkillResult.ok({
            "valid":           len(issues) == 0,
            "version":         ver,
            "project_id":      bp.get("project_id", ""),
            "component_count": len(bp.get("components", [])),
            "issues":          issues,
            "path":            path,
        })

    async def _implement_component(self, params: dict, confirmed: bool) -> SkillResult:
        """
        Phase II: Mark a component as IN_PROGRESS in blueprint.json.
        IN:  { component_name, blueprint_path? }
        OUT: { updated, version, component }
        """
        name   = params.get("component_name", "")
        path   = params.get("blueprint_path", BLUEPRINT_JSON)
        if not name:
            return SkillResult.fail("CBD_MISSING_PARAM: component_name is required")

        result = _update_component_status(path, name, "IN_PROGRESS")
        return result

    async def _validate_component(self, params: dict, confirmed: bool) -> SkillResult:
        """
        Phase II gate: Mark a component COMPLETE after tests pass.
        IN:  { component_name, test_report?, blueprint_path? }
        OUT: { updated, version, component }
        """
        name        = params.get("component_name", "")
        test_report = params.get("test_report", "")
        path        = params.get("blueprint_path", BLUEPRINT_JSON)
        if not name:
            return SkillResult.fail("CBD_MISSING_PARAM: component_name is required")
        if not test_report:
            return SkillResult.fail("CBD_GATE: test_report is required to mark a component COMPLETE. Run tests first.")

        result = _update_component_status(path, name, "COMPLETE")
        if result.success:
            result.output["test_report_acknowledged"] = True
        return result

    async def _version_read(self, params: dict, confirmed: bool) -> SkillResult:
        """
        Version Integrity: Read current version from blueprint.json before any modification.
        IN:  { path? }
        OUT: { blueprint_version, last_changed_by, last_summary, component_versions[] }
        """
        path = params.get("path", BLUEPRINT_JSON)
        if not os.path.isfile(path):
            return SkillResult.fail(f"CBD_BLUEPRINT_NOT_FOUND: '{path}'")

        try:
            with open(path, "r") as f:
                bp = json.load(f)

            changelog = bp.get("blueprint_changelog", [{}])
            last = changelog[-1] if changelog else {}

            comp_versions = [
                {
                    "name":    c.get("identity", {}).get("name", "?"),
                    "status":  c.get("status", "DRAFT"),
                    "version": (c.get("artifacts") or [{}])[0].get("version", "—"),
                }
                for c in bp.get("components", [])
            ]

            return SkillResult.ok({
                "path":              path,
                "blueprint_version": bp.get("blueprint_version", "unknown"),
                "project_id":        bp.get("project_id", ""),
                "last_changed_by":   last.get("changed_by", ""),
                "last_summary":      last.get("summary", ""),
                "last_date":         last.get("date", ""),
                "component_versions": comp_versions,
                "version_integrity_reminder": (
                    "You MUST read this before any modification. "
                    "State: 'Current blueprint_version is X.Y.Z. This change is [type] → incrementing to A.B.C.'"
                ),
            })
        except Exception as exc:
            return SkillResult.fail(f"CBD_VERSION_READ_ERROR: {exc}")

    # ═══════════════════════════════════════════════════════════════════════════
    # EXPERIENCED SYSTEM ACTIONS
    # ═══════════════════════════════════════════════════════════════════════════

    async def _experienced_lookup(self, params: dict, confirmed: bool) -> SkillResult:
        """
        Phase 0: Mandatory pre-task lookup. Search index before any debug/setup task.
        IN:  { symptom, error_message?, environment? }
        OUT: { decision: USE_EXISTING|PROCEED_NEW, matched_entry?, action_taken }
        """
        symptom = params.get("symptom", params.get("query", ""))
        if not symptom:
            return SkillResult.fail("CBD_EXP_MISSING_SYMPTOM: symptom is required for lookup")

        hits = await self._do_experienced_search(symptom, max_results=3)

        if hits:
            top = hits[0]
            return SkillResult.ok({
                "decision":       "USE_EXISTING",
                "matched_entry":  top,
                "all_hits":       hits,
                "action_taken":   f"Found {len(hits)} matching experience(s). Top match: {top.get('exp_id')}. Apply known fix before attempting anything new.",
            })
        else:
            return SkillResult.ok({
                "decision":     "PROCEED_NEW",
                "matched_entry": None,
                "action_taken": "No matching experience found. Proceed with task. On resolution, capture a new EXP entry.",
            })

    async def _experienced_search(self, params: dict, confirmed: bool) -> SkillResult:
        """
        Search EXP entries by keyword/symptom/category.
        IN:  { query, category?, severity?, max_results? }
        OUT: { results[], total_found }
        """
        query       = params.get("query", "")
        category    = params.get("category", "")
        severity    = params.get("severity", "")
        max_results = int(params.get("max_results", 5))

        if query == "*":
            query = ""

        hits = await self._do_experienced_search(query, category=category, severity=severity, max_results=max_results)
        return SkillResult.ok({"results": hits, "total_found": len(hits), "query": query})

    async def _experienced_capture(self, params: dict, confirmed: bool) -> SkillResult:
        """
        Phase III: Write a new EXP entry to disk. Status starts as DRAFT.
        IN:  Full ExperienceEntry payload (see experienced-blueprint.md)
        OUT: { exp_id, file_path, status, written_at }
        """
        title    = params.get("title", "")
        category = params.get("category", "general")
        severity = params.get("severity", "confusing")
        symptom  = params.get("discovery", {}).get("symptom", params.get("symptom", ""))

        if not title:
            return SkillResult.fail("CBD_EXP_MISSING_TITLE: title is required")
        if not symptom:
            return SkillResult.fail("CBD_EXP_MISSING_SYMPTOM: discovery.symptom is required")

        os.makedirs(EXPERIENCED_DIR, exist_ok=True)

        # Auto-assign EXP-ID
        existing = [f for f in os.listdir(EXPERIENCED_DIR) if re.match(r"EXP-\d+\.md", f)]
        next_num = len(existing) + 1
        exp_id   = f"EXP-{next_num:04d}"
        file_path = os.path.join(EXPERIENCED_DIR, f"{exp_id}.md")

        if os.path.isfile(file_path):
            return SkillResult.fail(f"CBD_EXP_DUPLICATE: {exp_id} already exists")

        now = datetime.now(timezone.utc).isoformat()
        environment = params.get("environment", {})
        root_cause  = params.get("root_cause", {})
        solution    = params.get("solution", {})
        prevention  = params.get("prevention", {})

        content = f"""# {exp_id} — {title}

**Status**: DRAFT
**Category**: {category}
**Severity**: {severity}
**Date Encountered**: {params.get('date_encountered', now[:10])}
**Created**: {now}

---

## Discovery

**Symptom**:
```
{symptom}
```

**Discovery Path**: {params.get('discovery', {}).get('discovery_path', 'Not documented')}

**Misleading Signals**:
{chr(10).join('- ' + s for s in params.get('discovery', {}).get('misleading_signals', [])) or '- None documented'}

---

## Environment

- **OS**: {environment.get('os', 'Not specified')}
- **Runtime Version**: {environment.get('runtime_version', 'Not specified')}
- **Framework Version**: {environment.get('framework_version', 'Not specified')}
- **Tool Versions**: {', '.join(environment.get('tool_versions', [])) or 'Not specified'}

---

## Root Cause

**Statement**: {root_cause.get('statement', 'DRAFT — root cause not yet confirmed')}

**Explanation**: {root_cause.get('explanation', 'Pending')}

**Trigger Conditions**: {root_cause.get('trigger_conditions', 'Pending')}

---

## Solution

**Fix**:
```
{solution.get('fix', 'Pending')}
```

**Why It Works**: {solution.get('why_it_works', 'Pending')}

**Alternatives Considered**:
{chr(10).join('- ' + a for a in solution.get('alternatives_considered', [])) or '- None documented'}

**Side Effects**: {solution.get('side_effects', 'None known')}

---

## Prevention

**Detection Hints**:
{chr(10).join('- ' + h for h in prevention.get('detection_hints', [])) or '- None documented'}

**Recommended Checks**:
{chr(10).join('- ' + c for c in prevention.get('recommended_checks', [])) or '- None documented'}

**Related Entries**: {', '.join(prevention.get('related_entries', [])) or 'None'}
"""

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

            # Update index
            await self._append_to_index(exp_id, title, category, severity, "DRAFT", environment, now[:10], file_path)

            logger.info("[cbd.exp_capture] exp_id=%s path=%s", exp_id, file_path)
            return SkillResult.ok({
                "exp_id":     exp_id,
                "file_path":  file_path,
                "status":     "DRAFT",
                "written_at": now,
                "next_step":  "Review and confirm root cause. Then call experienced_promote to CONFIRMED.",
            })
        except Exception as exc:
            return SkillResult.fail(f"CBD_EXP_WRITE_ERROR: {exc}")

    async def _experienced_promote(self, params: dict, confirmed: bool) -> SkillResult:
        """
        Transition an EXP entry status: DRAFT→CONFIRMED→STABLE→SUPERSEDED.
        IN:  { exp_id, new_status, superseded_by? }
        OUT: { exp_id, previous_status, new_status, transitioned_at }
        """
        exp_id     = params.get("exp_id", "")
        new_status = params.get("new_status", "").upper()
        superseded_by = params.get("superseded_by", "")

        if not exp_id or not new_status:
            return SkillResult.fail("CBD_EXP_MISSING_PARAM: exp_id and new_status are required")
        if new_status not in EXP_STATUSES:
            return SkillResult.fail(f"CBD_EXP_INVALID_STATUS: '{new_status}'. Valid: {EXP_STATUSES}")
        if new_status == "SUPERSEDED" and not superseded_by:
            return SkillResult.fail("CBD_EXP_MISSING_SUPERSEDED_BY: superseded_by is required for SUPERSEDED status")

        file_path = os.path.join(EXPERIENCED_DIR, f"{exp_id}.md")
        if not os.path.isfile(file_path):
            return SkillResult.fail(f"CBD_EXP_NOT_FOUND: '{exp_id}'")

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Extract current status
            m = re.search(r"\*\*Status\*\*: (\w+)", content)
            prev_status = m.group(1) if m else "UNKNOWN"

            # Validate transition
            valid_transitions = {
                "DRAFT":     ["CONFIRMED"],
                "CONFIRMED": ["STABLE", "SUPERSEDED"],
                "STABLE":    ["SUPERSEDED"],
                "SUPERSEDED": [],
            }
            allowed = valid_transitions.get(prev_status, [])
            if new_status not in allowed:
                return SkillResult.fail(
                    f"CBD_EXP_INVALID_TRANSITION: {prev_status} → {new_status} not allowed. "
                    f"Allowed from {prev_status}: {allowed}"
                )

            now     = datetime.now(timezone.utc).isoformat()
            content = content.replace(f"**Status**: {prev_status}", f"**Status**: {new_status}", 1)
            if superseded_by:
                content += f"\n**Superseded By**: {superseded_by}\n"

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

            logger.info("[cbd.exp_promote] %s %s→%s", exp_id, prev_status, new_status)
            return SkillResult.ok({
                "exp_id":          exp_id,
                "previous_status": prev_status,
                "new_status":      new_status,
                "transitioned_at": now,
            })
        except Exception as exc:
            return SkillResult.fail(f"CBD_EXP_PROMOTE_ERROR: {exc}")

    async def _experienced_rebuild_index(self, params: dict, confirmed: bool) -> SkillResult:
        """
        Rebuild experienced/index.md from all EXP-*.md files on disk.
        IN:  {}
        OUT: { rebuilt, entry_count, index_path }
        """
        os.makedirs(EXPERIENCED_DIR, exist_ok=True)
        entries = []

        for fname in sorted(os.listdir(EXPERIENCED_DIR)):
            if not re.match(r"EXP-\d+\.md", fname):
                continue
            fpath = os.path.join(EXPERIENCED_DIR, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    text = f.read()

                exp_id   = fname.replace(".md", "")
                title    = re.search(r"# EXP-\d+ — (.+)", text)
                status   = re.search(r"\*\*Status\*\*: (\w+)", text)
                category = re.search(r"\*\*Category\*\*: (\S+)", text)
                severity = re.search(r"\*\*Severity\*\*: (\S+)", text)
                date     = re.search(r"\*\*Date Encountered\*\*: (\S+)", text)

                entries.append({
                    "exp_id":   exp_id,
                    "title":    title.group(1).strip() if title else "—",
                    "status":   status.group(1) if status else "DRAFT",
                    "category": category.group(1) if category else "—",
                    "severity": severity.group(1) if severity else "—",
                    "date":     date.group(1) if date else "—",
                    "path":     fpath,
                })
            except Exception as exc:
                logger.warning("[cbd.rebuild_index.skip] %s: %s", fname, exc)

        # Write index
        rows = "\n".join(
            f"| {e['exp_id']} | {e['title']} | {e['category']} | {e['severity']} | "
            f"{e['status']} | {e['date']} | {e['path']} |"
            for e in entries
        )
        index_content = f"""# Experienced — Knowledge Index

**Last Rebuilt**: {datetime.now(timezone.utc).isoformat()}
**Total Entries**: {len(entries)}

---

| EXP-ID | Title | Category | Severity | Status | Date | File |
|---|---|---|---|---|---|---|
{rows}
"""
        try:
            _atomic_write(EXP_INDEX, index_content)
            logger.info("[cbd.rebuild_index] entries=%d", len(entries))
            return SkillResult.ok({
                "rebuilt":     True,
                "entry_count": len(entries),
                "index_path":  EXP_INDEX,
            })
        except Exception as exc:
            return SkillResult.fail(f"CBD_EXP_REBUILD_ERROR: {exc}")

    # ═══════════════════════════════════════════════════════════════════════════
    # META ACTIONS
    # ═══════════════════════════════════════════════════════════════════════════

    async def _get_template(self, params: dict, confirmed: bool) -> SkillResult:
        """Return a template string for a requested artifact type."""
        template_type = params.get("type", "component")
        templates = {
            "component": _COMPONENT_TEMPLATE,
            "exp_entry": _EXP_ENTRY_TEMPLATE,
            "blueprint_json": _BLUEPRINT_JSON_TEMPLATE,
            "test_report": _TEST_REPORT_TEMPLATE,
            "security_report": _SECURITY_REPORT_TEMPLATE,
        }
        t = templates.get(template_type)
        if t is None:
            return SkillResult.fail(f"CBD_UNKNOWN_TEMPLATE: '{template_type}'. Valid: {', '.join(templates)}")
        return SkillResult.ok({"type": template_type, "template": t})

    async def _get_skills_registry(self, params: dict, confirmed: bool) -> SkillResult:
        """Return the CBD skills registry (cbd-skills.md summary)."""
        return SkillResult.ok({
            "skills": [
                {"id": "SKL-001", "name": "File System Operations",        "phase": "-1,I,II,III", "critical": True},
                {"id": "SKL-002", "name": "Blueprint Read & Write",         "phase": "I,II",        "critical": True},
                {"id": "SKL-003", "name": "Code Execution & Runtime",       "phase": "II",          "critical": True},
                {"id": "SKL-004", "name": "Test Creation & Reporting",      "phase": "II,III",      "critical": True},
                {"id": "SKL-005", "name": "Security Validation",            "phase": "I,II",        "critical": True},
                {"id": "SKL-006", "name": "Documentation & Design Writing", "phase": "I,III",       "critical": True},
                {"id": "SKL-007", "name": "Log Reading & Debug Investigation","phase": "0,II",      "critical": True},
            ],
            "version": "1.0.0",
        })

    # ═══════════════════════════════════════════════════════════════════════════
    # PRIVATE HELPERS
    # ═══════════════════════════════════════════════════════════════════════════

    async def _do_experienced_search(
        self,
        query: str,
        category: str = "",
        severity: str = "",
        max_results: int = 5,
    ) -> list:
        """Keyword search against experienced/index.md and EXP-*.md files."""
        results = []
        if not os.path.isdir(EXPERIENCED_DIR):
            return results

        query_l    = query.lower()
        category_l = category.lower()
        severity_l = severity.lower()

        for fname in sorted(os.listdir(EXPERIENCED_DIR)):
            if not re.match(r"EXP-\d+\.md", fname):
                continue
            fpath = os.path.join(EXPERIENCED_DIR, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    text = f.read()

                text_l = text.lower()
                # Filter by category/severity if provided
                if category_l and category_l not in text_l:
                    continue
                if severity_l and severity_l not in text_l:
                    continue
                # Keyword match
                if query_l and query_l not in text_l:
                    continue

                exp_id   = fname.replace(".md", "")
                title    = re.search(r"# EXP-\d+ — (.+)", text)
                status   = re.search(r"\*\*Status\*\*: (\w+)", text)
                cat_m    = re.search(r"\*\*Category\*\*: (\S+)", text)
                sev_m    = re.search(r"\*\*Severity\*\*: (\S+)", text)

                # Determine relevance signal
                relevance = "title" if query_l in (title.group(1).lower() if title else "") \
                    else "symptom" if "## discovery" in text_l and query_l in text_l[text_l.find("## discovery"):] \
                    else "body"

                results.append({
                    "exp_id":         exp_id,
                    "title":          title.group(1).strip() if title else "—",
                    "status":         status.group(1) if status else "DRAFT",
                    "category":       cat_m.group(1) if cat_m else "—",
                    "severity":       sev_m.group(1) if sev_m else "—",
                    "relevance_signal": relevance,
                    "file_path":      fpath,
                })
                if len(results) >= max_results:
                    break
            except Exception:
                continue

        return results

    async def _append_to_index(
        self, exp_id, title, category, severity, status, environment, date, file_path
    ):
        """Append a new row to experienced/index.md."""
        os.makedirs(EXPERIENCED_DIR, exist_ok=True)
        env_summary = environment.get("runtime_version", "") if isinstance(environment, dict) else ""
        row = f"| {exp_id} | {title} | {category} | {severity} | {status} | {date} | {file_path} |\n"

        if not os.path.isfile(EXP_INDEX):
            header = ("# Experienced — Knowledge Index\n\n"
                      "| EXP-ID | Title | Category | Severity | Status | Date | File |\n"
                      "|---|---|---|---|---|---|---|\n")
            with open(EXP_INDEX, "w", encoding="utf-8") as f:
                f.write(header)

        with open(EXP_INDEX, "a", encoding="utf-8") as f:
            f.write(row)


# ── Private helpers ────────────────────────────────────────────────────────────

def _atomic_write(path: str, content: str):
    import tempfile
    dir_ = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(dir_, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=dir_, suffix=".tmp", delete=False, encoding="utf-8") as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    os.replace(tmp_path, path)


def _update_component_status(path: str, component_name: str, new_status: str) -> SkillResult:
    if not os.path.isfile(path):
        return SkillResult.fail(f"CBD_BLUEPRINT_NOT_FOUND: '{path}'")
    try:
        with open(path, "r") as f:
            bp = json.load(f)
        found = False
        for comp in bp.get("components", []):
            if comp.get("identity", {}).get("name") == component_name:
                comp["status"] = new_status
                found = True
                break
        if not found:
            return SkillResult.fail(f"CBD_COMPONENT_NOT_FOUND: '{component_name}' in blueprint")

        now = datetime.now(timezone.utc).isoformat()
        bp.setdefault("blueprint_changelog", []).append({
            "version":    bp.get("blueprint_version", "1.0.0"),
            "date":       now,
            "changed_by": "agent",
            "summary":    f"Component '{component_name}' status → {new_status}",
        })
        _atomic_write(path, json.dumps(bp, indent=2))
        return SkillResult.ok({
            "updated":    True,
            "component":  component_name,
            "new_status": new_status,
            "path":       path,
        })
    except Exception as exc:
        return SkillResult.fail(f"CBD_STATUS_UPDATE_ERROR: {exc}")


def _default_component_block(c: dict) -> dict:
    return {
        "identity": {
            "name":             c.get("name", "Unnamed"),
            "reason":           c.get("reason", "—"),
            "logical_function": c.get("function", "—"),
        },
        "status": "DRAFT",
        "interface": {
            "in_schema":    c.get("in_schema", {}),
            "out_schema":   c.get("out_schema", {}),
            "error_schema": c.get("error_schema", {}),
        },
        "artifacts": [],
        "observability": {"trace_points": [], "failure_map": []},
        "experience_hook": {"consumer": True, "producer": False},
    }


# ── Templates ──────────────────────────────────────────────────────────────────

_COMPONENT_TEMPLATE = """\
### Component — `ComponentName`

**Identity**
- **Name**: ComponentName
- **Reason**: Why this component exists
- **Logical Function**: Retrieval | Write | Orchestration | Validation | ...

**IN-Schema**
```json
{ "field": "type (required|optional)" }
```

**OUT-Schema**
```json
{ "result": "type" }
```

**Error-Schema**
```json
{ "error_code": "ERR_CODE", "message": "str" }
```

**Trace Log Points**
- `event_name` — description

**Failure Map**
- try-catch on all I/O operations
- Return Error-Schema on all failures — never throw to caller
"""

_EXP_ENTRY_TEMPLATE = """\
# EXP-XXXX — Title

**Status**: DRAFT
**Category**: dependency-conflict|environment-setup|configuration|build-tooling|runtime-crash|integration|performance-degradation|security-constraint
**Severity**: blocking|degrading|confusing
**Date Encountered**: YYYY-MM-DD

## Discovery
**Symptom**: verbatim error text here
**Discovery Path**: how was this found
**Misleading Signals**: what led you down the wrong path

## Environment
- OS:
- Runtime Version:
- Framework Version:

## Root Cause
**Statement**: one precise sentence
**Explanation**: full explanation
**Trigger Conditions**: what conditions cause this

## Solution
**Fix**: exact steps or code
**Why It Works**: explanation
**Alternatives Considered**:
**Side Effects**: none known

## Prevention
**Detection Hints**:
**Recommended Checks**:
**Related Entries**:
"""

_BLUEPRINT_JSON_TEMPLATE = """\
{
  "_version": "1.0.0",
  "project_id": "your-project-id",
  "methodology": "CBD-Interface-First v2.2",
  "blueprint_version": "1.0.0",
  "blueprint_changelog": [
    { "version": "1.0.0", "date": "ISO8601", "changed_by": "agent", "summary": "Initial blueprint" }
  ],
  "experienced_system": {
    "enabled": true,
    "index_path": "experienced/index.md",
    "entries_path": "experienced/",
    "mandatory_lookup_before": ["debugging", "environment-setup"]
  },
  "components": []
}
"""

_TEST_REPORT_TEMPLATE = """\
## Test Report — ComponentName vX.Y.Z
**Run Date**: ISO8601
**Result**: PASS | FAIL | PARTIAL

### Summary
| Total | Passed | Failed | Skipped |
|-------|--------|--------|---------|
| N     | N      | N      | N       |

### Failed Tests
| Test Name | Expected | Actual | Error |
|-----------|----------|--------|-------|

### Coverage
- IN-Schema happy path: covered / not covered
- Error-Schema paths: covered / not covered
- Failure Map scenarios: covered / not covered
"""

_SECURITY_REPORT_TEMPLATE = """\
## Security Validation Report — ComponentName vX.Y.Z
**Run Date**: ISO8601
**Result**: CLEAR | FINDINGS

### Findings
| Severity | Area | Description | Resolution |
|----------|------|-------------|------------|

### Checks Passed
- [ ] IN-Schema input validation enforced
- [ ] No hardcoded secrets in source
- [ ] Secrets loaded from environment
- [ ] Error responses do not leak internals
- [ ] Dependency scan: no CRITICAL/HIGH unresolved
- [ ] Auth/authz defined for exposed endpoints
"""
