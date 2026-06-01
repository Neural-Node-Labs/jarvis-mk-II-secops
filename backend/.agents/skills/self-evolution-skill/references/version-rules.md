# Version Rules — Decision Table

> Load this during Phase 5 before modifying any file.
> The agent must always read the current version before writing. Never guess or hardcode.

## Semantic Version Format: MAJOR.MINOR.PATCH

| Digit | When to Increment |
|-------|------------------|
| **MAJOR** | Breaking change — existing interface, schema, or contract changes in a non-backward-compatible way |
| **MINOR** | New feature, new component, or new capability added without breaking existing contracts |
| **PATCH** | Bug fix, defect resolution, or internal correction that does not alter any interface |

---

## Blueprint Version Rules

| Change | Increment |
|--------|-----------|
| New component added | MINOR |
| Component removed or renamed | MAJOR |
| IN/OUT/Error schema changed (breaking) | MAJOR |
| New field added to schema (backward compatible) | MINOR |
| Trace point or failure map updated | PATCH |
| Documentation or description corrected | PATCH |
| Requirement added by user (non-breaking) | MINOR |
| Requirement added by user (breaking) | MAJOR |

---

## Code File Version Rules

| Change | Increment |
|--------|-----------|
| Bug fix, internal correction | PATCH |
| New function, new endpoint, new behavior | MINOR |
| Changed function signature or API contract | MAJOR |
| Dependency upgrade (non-breaking) | PATCH |
| Dependency upgrade (breaking API change) | MAJOR |

---

## Mandatory Declaration Format

Before modifying any file, state aloud:

> *"Current version of `{filename}` is `{x.y.z}`. This change is a {bug fix | new feature |
> breaking change} → incrementing to `{a.b.c}`."*

Skipping this declaration is a **protocol violation** equivalent to writing code without a try-catch.

---

## Version Header Formats by Language

### Python
```python
# version: 1.0.0
# changelog:
#   1.0.0 - 2026-06-01 - Initial implementation
#   1.0.1 - 2026-06-02 - Fixed null handling in parser
```

### JavaScript / TypeScript
```javascript
// version: 1.0.0
// changelog:
//   1.0.0 - 2026-06-01 - Initial implementation
```

### Java
```java
// version: 1.0.0
// changelog:
//   1.0.0 - 2026-06-01 - Initial implementation
```

### Markdown (SKILL.md, blueprint.md)
```markdown
<!-- version: 1.0.0 -->
<!-- changelog:
  1.0.0 - 2026-06-01 - Initial draft
-->
```

### JSON
```json
{
  "_version": "1.0.0",
  "_changelog": [
    { "version": "1.0.0", "date": "2026-06-01", "summary": "Initial" }
  ]
}
```

### Shell / Bash
```bash
# version: 1.0.0
# changelog:
#   1.0.0 - 2026-06-01 - Initial script
```

---

## Keeping Blueprint and Code in Sync

After every code file change:
1. Update `artifacts[n].version` in `blueprint.json` to the new code version
2. Append to `artifacts[n].changelog`
3. This always triggers at minimum a **PATCH** to `blueprint_version`

A blueprint whose artifact versions do not match the files on disk is **out of sync**.
Correct before the next Phase 5 turn.
