---
name: self-evolution
description: >
  AI self-evolution and capability enhancement skill. Use this skill whenever the user asks Claude
  to improve itself, enhance a skill, upgrade existing code, evolve a component, self-modify, or
  perform any form of autonomous code enhancement. Triggers include: "improve this skill",
  "evolve the system", "enhance the capability", "self-upgrade", "update and redeploy", "refactor
  and validate", "make it better and deploy", "autonomous enhancement", or any request where
  Claude must analyse, redesign, test, and redeploy its own code or skill artifacts. Always use
  this skill when the task involves reading existing code → designing changes → implementing →
  testing → deploying, even if the user phrases it casually.
version: 1.0.0
methodology: CBD-v2.2
---

# Self-Evolution Skill

A structured, CBD v2.2-compliant protocol for AI-driven code analysis, enhancement, testing, and
redeployment. This skill governs every autonomous self-improvement cycle Claude executes.

> **Before starting**: Check `experienced/index.md` if it exists in the workspace. This is a
> mandatory Phase 0 lookup per CBD v2.2.

---

## Workflow Overview (9 Phases)

```
Phase 0  → Experience Lookup
Phase 1  → Workspace Bootstrap
Phase 2  → Source Discovery & Copy
Phase 3  → Code Analysis
Phase 4  → Blueprint Generation & Approval
Phase 5  → Implementation
Phase 6  → Build & Runtime Validation
Phase 7  → UI & API Test Suite
Phase 8  → Deploy & Verify
Phase 9  → Restart & Health Check
```

No phase may be skipped. Each phase gates the next.

---

## Phase 0 — Experience Lookup (MANDATORY FIRST)

Before any other action:

1. Check if `{workspace}/experienced/index.md` exists
2. If it exists → search for entries matching the current task domain
3. Log result: `LOOKUP_HIT` (matched entry) or `LOOKUP_MISS` (no match)
4. If LOOKUP_HIT → apply the known solution pattern; note the EXP-ID used
5. If LOOKUP_MISS → proceed; flag this domain for potential new entry at Phase 9

**Non-blocking rule**: If `index.md` is missing or unreadable, log the error and continue.
The knowledge system must never block execution.

```
[ISO8601] [PHASE-0] LOOKUP_HIT   query="<domain>" matched=EXP-XXXX
[ISO8601] [PHASE-0] LOOKUP_MISS  query="<domain>"
```

---

## Phase 1 — Workspace Bootstrap

Create a clean, isolated temporary workspace before touching any original files.

### Steps

1. **Generate workspace ID**: `ws-{YYYYMMDD}-{HHMM}-{slug}` (slug = 3-word task descriptor)
2. **Create directory tree**:

```
/tmp/evo/{workspace_id}/
├── src/                  ← copy of original source files
├── tests/                ← generated test scripts
├── reports/              ← analysis outputs
├── blueprint.md          ← human-readable change design (Phase 4)
├── blueprint.json        ← machine-readable contracts (Phase 4)
├── experienced/
│   └── index.md          ← EXP knowledge index (initialize if absent)
└── evolution.log         ← full audit trail for this session
```

3. **Initialize `evolution.log`** with header:

```
[ISO8601] EVOLUTION SESSION STARTED
Workspace : /tmp/evo/{workspace_id}
Task      : {one-line task description}
Phase     : 0 (Experience Lookup)
```

4. **Initialize `experienced/index.md`** if not copied from source:

```markdown
# Experience Index
| EXP-ID | Category | Title | Status | Date |
|--------|----------|-------|--------|------|
```

### Validation Gate

- [ ] Workspace directory tree exists and is writable
- [ ] `evolution.log` initialized
- [ ] `experienced/index.md` present

---

## Phase 2 — Source Discovery & Copy

Identify every file required to run the target system/skill and copy it into the workspace.

### Steps

1. **Enumerate source files** — scan the target path recursively:
   - Include: `*.py`, `*.js`, `*.ts`, `*.java`, `*.json`, `*.yaml`, `*.md`, `*.sh`, `*.env.example`
   - Exclude: `node_modules/`, `__pycache__/`, `.git/`, `*.log`, `dist/`, `build/`

2. **Preserve directory structure** — mirror the original layout under `workspace/src/`

3. **Record manifest** — write `workspace/reports/source-manifest.txt`:

```
SOURCE MANIFEST
Generated : {ISO8601}
Origin    : {original_path}
---
{relative_path} | {size_bytes} | {sha256_first8}
...
TOTAL: {n} files
```

4. **Read version headers** — for every file with a version comment/header, extract and log:

```
[ISO8601] [PHASE-2] VERSION_READ  file="{path}" version="{x.y.z}"
```

### Validation Gate

- [ ] All required files present in `workspace/src/`
- [ ] Source manifest written
- [ ] Version headers logged for all versioned files

---

## Phase 3 — Code Analysis

Perform a structured analysis of all copied source files. Produce a written report.

### Analysis Dimensions

For each component/file:

| Dimension | What to assess |
|-----------|---------------|
| **Purpose** | What does this file/component do? |
| **Interfaces** | What are its inputs, outputs, and API contracts? |
| **Dependencies** | What external libs or internal files does it import? |
| **Versioning** | Does it carry a version header? Is it current? |
| **Defects** | Missing error handling, hardcoded values, logic gaps |
| **CBD Compliance** | Does it follow CBD v2.2 principles? (atomicity, interface-first, black-box) |
| **Experience Coverage** | Any risk areas flagged in Phase 0 lookup? |
| **Enhancement Opportunities** | What improvements are needed per the task? |

### Output

Write `workspace/reports/analysis.md`:

```markdown
# Code Analysis Report
Generated : {ISO8601}
Workspace : {workspace_id}

## Summary
{2-3 sentence executive summary}

## Component Analysis

### {component_name}
- **File**: `{path}`
- **Current Version**: `{x.y.z}`
- **Purpose**: {one line}
- **Interface**: IN → {schema summary} | OUT → {schema summary}
- **Defects Found**: {list or "None"}
- **CBD Compliance**: {PASS | PARTIAL | FAIL — with reason}
- **Enhancement Targets**: {list}

## Cross-Cutting Concerns
{Shared issues affecting multiple components}

## Risk Assessment
| Risk | Severity | Component | Mitigation |
|------|----------|-----------|------------|
...

## Feasibility Verdict
{GO | CAUTION} — {one-line rationale}
```

---

## Phase 4 — Blueprint Generation & Approval

Design all required changes before writing a single line of implementation code.

> **CBD v2.1 Rule**: No implementation begins without explicit written approval of both blueprint
> files.

### Step 4a — Generate `blueprint.md`

Write a human-readable document covering:

1. **Change Summary** — what is being changed and why
2. **Component Table** — for each affected component:
   - Current state vs target state
   - Interface changes (IN/OUT schema diffs)
   - Files to be created / modified / deleted
   - Version increment type (MAJOR / MINOR / PATCH) and rationale
3. **Dependency Map** — libraries, internal imports affected
4. **Stability & Trace Strategy** — per CBD Section 7
5. **Test Plan** — what Phase 7 scripts will validate
6. **Rollback Plan** — how to revert if Phase 8 validation fails

### Step 4b — Generate `blueprint.json`

Follow the CBD v2.2 Blueprint JSON Structure exactly (see `references/cbd-schema.md`).

Increment `blueprint_version` from the value read in Phase 2 (or start at `1.0.0` if new).
Append to `blueprint_changelog`.

### Step 4c — Request Approval

Present both files to the user with this gate:

```
─────────────────────────────────────────────
 BLUEPRINT READY FOR APPROVAL
─────────────────────────────────────────────
 blueprint.md   → {workspace}/blueprint.md
 blueprint.json → {workspace}/blueprint.json

 Please review both files.
 Reply APPROVED to proceed to implementation.
 Reply REVISE + {feedback} to trigger a revision.
─────────────────────────────────────────────
```

**Do not proceed to Phase 5 until APPROVED is received.**

---

## Phase 5 — Implementation

Execute all changes defined in the approved blueprint. One component at a time.

### Per-File Protocol

For every file being modified or created:

1. **State the version read**:
   > *"Current version of `{file}` is `{x.y.z}`. This change is a {bug fix | new feature |
   > breaking change} → incrementing to `{new_version}`."*

2. **Apply changes** as specified in the blueprint

3. **Update version header** in the file

4. **Append changelog line** in the file header:
   ```
   {new_version} - {ISO8601-date} - {one-line summary of change}
   ```

5. **Update `blueprint.json`** — sync artifact version and append to artifact changelog

6. **Log to `evolution.log`**:
   ```
   [ISO8601] [PHASE-5] FILE_MODIFIED  file="{path}" old="{x.y.z}" new="{a.b.c}"
   ```

### Completion Check

After all files are processed, verify against the blueprint's file list:
- [ ] Every file listed in blueprint has been created/modified
- [ ] Every file carries the correct new version header
- [ ] `blueprint.json` artifact versions match files on disk

---

## Phase 6 — Build & Runtime Validation

Verify the changed code is syntactically valid and functionally operational.

### Steps

1. **Syntax check** — language-appropriate static analysis:
   - Python: `python -m py_compile {file}` or `pylint`
   - JavaScript/TypeScript: `node --check` or `tsc --noEmit`
   - Java: `javac`
   - JSON/YAML: schema validation

2. **Dependency check** — confirm all imports resolve:
   - Python: `pip check`
   - Node: `npm ls`

3. **Smoke test** — run the minimal execution path:
   - If there is an existing test suite, run it
   - If not, construct a minimal invocation that exercises the changed code paths

4. **Log results** to `evolution.log`:
   ```
   [ISO8601] [PHASE-6] BUILD_PASS   component="{name}"
   [ISO8601] [PHASE-6] BUILD_FAIL   component="{name}" error="{message}"
   ```

**BUILD_FAIL stops the evolution cycle.** Return to Phase 5 to fix before proceeding.

---

## Phase 7 — UI & API Test Suite

Generate and execute a dedicated test script for this evolution cycle.

### Script Location

`workspace/tests/evolution_test_{workspace_id}.{py|js|sh}`

### Required Test Coverage

#### API Tests (for every exposed endpoint or function)

```python
# Template for each API test
def test_{component}_{scenario}():
    # Arrange
    input_data = {IN_SCHEMA example}
    # Act
    result = call_component(input_data)
    # Assert
    assert result["status"] == "success"
    assert "expected_field" in result
    # Trace
    log(f"[PASS] {component}.{scenario}")
```

Test scenarios to cover:
- **Happy path** — valid input → expected output
- **Edge cases** — boundary values, empty inputs, max sizes
- **Error path** — invalid input → correct Error-Schema returned
- **Regression** — previously working behavior still works

#### UI Tests (if applicable)

If the component has a user interface:
- Element presence checks
- Interaction flows (click, submit, navigate)
- Responsive breakpoint validation
- Accessibility checks (alt text, ARIA labels, keyboard nav)

### Test Report

Write `workspace/reports/test-report.md` after execution:

```markdown
# Test Report
Run at  : {ISO8601}
Suite   : evolution_test_{workspace_id}

| Test | Component | Scenario | Status | Duration |
|------|-----------|----------|--------|----------|
| T001 | {name}    | happy path | PASS  | 0.12s    |
...

## Summary
Total: {n} | PASS: {p} | FAIL: {f} | SKIP: {s}

## Failed Tests
{Detail for each failure}

## Verdict
{ALL_PASS | FAILURES_PRESENT}
```

**FAILURES_PRESENT stops the evolution cycle.** Fix in Phase 5, re-run Phase 6 and 7.

---

## Phase 8 — Deploy & Verify

Copy the validated workspace files back to the original source location.

### Steps

1. **Pre-deploy backup** — copy current originals to `workspace/reports/backup/`:
   ```
   [ISO8601] [PHASE-8] BACKUP_CREATED  path="workspace/reports/backup/"
   ```

2. **Copy files** — transfer each file from `workspace/src/` to its original path:
   ```
   [ISO8601] [PHASE-8] FILE_DEPLOYED  src="{workspace}/src/{path}" dst="{original_path}"
   ```

3. **Post-copy verification** — for every deployed file:
   - Confirm file exists at destination
   - Confirm file size matches source
   - Confirm version header at destination matches workspace version

4. **Write deployment manifest** `workspace/reports/deploy-manifest.txt`:
   ```
   DEPLOYMENT MANIFEST
   Deployed at : {ISO8601}
   ---
   {original_path} | {version} | {sha256_first8} | VERIFIED
   ...
   TOTAL DEPLOYED: {n} files
   ALL VERIFIED: {YES | NO — list failures}
   ```

**Any verification failure triggers an immediate rollback from `workspace/reports/backup/`.**

---

## Phase 9 — Restart & Health Check

Restart the service/skill and confirm it is operational.

### Steps

1. **Identify restart mechanism** (from blueprint or user-specified):
   - Skill reload: notify the skill system to reload the updated SKILL.md
   - Service restart: `systemctl restart {service}` / `docker restart {container}` / `pm2 restart {app}`
   - Process restart: kill and re-launch the process

2. **Execute restart**:
   ```
   [ISO8601] [PHASE-9] RESTART_INITIATED  mechanism="{type}" target="{name}"
   ```

3. **Health check** — wait up to 30 seconds, polling every 5 seconds:
   - Ping health endpoint (`/health`, `/ping`, or equivalent)
   - Verify process is running
   - Run one smoke test from Phase 7

4. **Log outcome**:
   ```
   [ISO8601] [PHASE-9] HEALTH_CHECK  status="UP | DOWN" latency="{ms}ms"
   ```

5. **Experience entry** — if any novel issue was encountered during this evolution:
   - Create a DRAFT entry in `workspace/experienced/`
   - Assign next EXP-ID from the index
   - Mark status DRAFT; confirm to CONFIRMED after fix is verified
   - Update `workspace/experienced/index.md`
   - Copy experience files back to source with deployed files

6. **Session close** in `evolution.log`:
   ```
   [ISO8601] EVOLUTION SESSION COMPLETE
   Workspace  : {workspace_id}
   Phases     : 0–9
   Files      : {n} deployed
   Tests      : {p}/{total} passed
   EXP Entries: {n} new entries
   Status     : SUCCESS | PARTIAL | FAILED
   ```

---

## Version Integrity — Quick Reference

Before touching any file, always state:
> *"Current version of `{file}` is `{x.y.z}`. Change type: {PATCH|MINOR|MAJOR} → new version `{a.b.c}`."*

| Change type | Increment |
|-------------|-----------|
| Bug fix / internal correction | PATCH |
| New feature / new component | MINOR |
| Breaking interface change | MAJOR |

---

## Abort Conditions

The evolution cycle must halt and rollback on:

| Condition | Phase | Action |
|-----------|-------|--------|
| Blueprint not approved | 4 | Wait; do not proceed |
| BUILD_FAIL | 6 | Fix in Phase 5; restart from Phase 5 |
| FAILURES_PRESENT | 7 | Fix in Phase 5; restart from Phase 5 |
| Deploy verification failure | 8 | Rollback from backup immediately |
| Health check DOWN after 30s | 9 | Rollback; escalate to user |

---

## References

- `references/cbd-schema.md` — Full Blueprint JSON schema (CBD v2.2)
- `references/experience-entry-template.md` — EXP entry format (five mandatory blocks)
- `references/version-rules.md` — Semantic versioning decision table

For the full CBD v2.2 methodology, see the uploaded `cbd.md` document.
