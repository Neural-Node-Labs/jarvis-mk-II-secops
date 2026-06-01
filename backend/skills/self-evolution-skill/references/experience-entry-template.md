# Experience Entry Template

> Five mandatory blocks. All must be complete before status moves from DRAFT to CONFIRMED.

## File Naming

`experienced/EXP-{NNNN}-{slug}.md`

Where NNNN is the zero-padded next ID from `index.md` and slug is a 3-word kebab-case descriptor.

## Template

```markdown
---
exp_id    : EXP-NNNN
title     : One-line human-readable title
category  : dependency-conflict | environment-setup | logic-error | integration-failure | performance | security | other
severity  : LOW | MEDIUM | HIGH | CRITICAL
status    : DRAFT | CONFIRMED | STABLE | SUPERSEDED
date      : ISO8601
environment:
  os      : "Ubuntu 24.04"
  runtime : "Python 3.12 / Node 20 / Java 21 / etc."
  versions:
    - "{library}=={version}"
superseded_by: null  # or EXP-XXXX if SUPERSEDED
---

## Block 1 — Identity
*(Captured in frontmatter above)*

## Block 2 — Discovery
**Symptom (verbatim)**:
> Exact error message or observable behavior that triggered investigation

**Discovery Path**:
Step-by-step account of how the issue was found.

**Misleading Signals**:
What looked like the cause but wasn't. What dead ends were hit.

## Block 3 — Root Cause
**One-Sentence Statement**:
{Concise cause}

**Technical Explanation**:
Detailed technical account of why this failure occurs.

**Trigger Conditions**:
- Condition A
- Condition B

## Block 4 — Solution
**Exact Fix**:
```{language}
{code or command that resolves the issue}
```

**Why It Works**:
Technical explanation of why this fix resolves the root cause.

**Alternatives Considered**:
| Alternative | Reason Rejected |
|-------------|-----------------|
| ...         | ...             |

**Side Effects**:
Any known side effects or trade-offs of this fix.

## Block 5 — Prevention
**Detection Hints**:
How to spot this issue early in future runs.

**Recommended Checks**:
- Check A (add to Phase 0 lookup triggers)
- Check B

**Related EXP-IDs**:
- EXP-XXXX — {relationship description}
```

## Index Entry Format

After confirming an entry, append to `experienced/index.md`:

```markdown
| EXP-{NNNN} | {category} | {title} | CONFIRMED | {ISO8601-date} |
```

## Lifecycle Transitions

```
DRAFT      → CONFIRMED   : All 5 blocks complete, fix verified in Phase 6/7
CONFIRMED  → STABLE      : Entry successfully reused in a subsequent evolution run
CONFIRMED  → SUPERSEDED  : Replaced by a newer entry; add superseded_by field
```
