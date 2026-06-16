# AI Code Construction & Modification Directive
## (Using sed, str_replace, and similar editing tools)

## Purpose
This directive defines how an AI should create and edit code files using targeted editing tools (e.g., `str_replace`, `sed`, patch-style edits) rather than wholesale rewrites — mirroring how Claude minimizes risk and diff size when modifying existing code.

## 1. Read Before You Edit
- Always view/read the current file content immediately before editing it — never edit from memory or a stale earlier view.
- After any successful edit, treat prior views of that file as stale; re-view before making further edits to the same file.
- For multi-file changes, understand how the pieces relate before editing any of them.

## 2. Prefer Targeted Edits Over Full Rewrites
- For existing files, use a precise find-and-replace style edit (e.g., `str_replace`, a small `sed` substitution, or a patch) instead of regenerating the whole file.
- Reserve full-file creation/rewrite for: new files, or existing files where the change is so pervasive that a rewrite is genuinely clearer and safer.
- Smaller, targeted diffs are easier to review and less likely to introduce unintended changes.

## 3. Make the Match Unambiguous
- The string/pattern being replaced must match **exactly once** in the file. If it could match multiple locations, add enough surrounding context (preceding/following lines) to make it unique.
- Copy the exact existing text — including whitespace, indentation, and punctuation — don't retype from memory or assumption.
- When using `sed`, prefer anchored or context-specific patterns over broad/global substitutions (`s/.../.../g` across a whole file) unless a global change is truly intended.

## 4. Using sed Specifically
- Use `sed -n` to preview matches before applying a destructive substitution, especially for anything with `g` (global) flags.
- Escape special regex characters (`. * [ ] ^ $ \`) in the search pattern when they should be literal.
- For multi-line or structural changes, prefer a proper edit tool over `sed`, which is line-oriented and error-prone for anything beyond simple substitutions.
- Always verify the result afterward (re-view the file or run a diff) — `sed` applies silently and can produce unexpected matches.

## 5. Keep Changes Minimal and Scoped
- Change only what's needed to accomplish the task — don't reformat, reindent, or rename unrelated code as a side effect of an edit.
- Avoid edits that incidentally change whitespace-only lines, line endings, or unrelated formatting, as this creates noisy diffs.
- If a broader refactor seems warranted, flag it to the user as a separate suggestion rather than bundling it into the current change.

## 6. Verify After Every Edit
- After each edit, re-view the changed region to confirm it looks correct (right indentation, no leftover fragments, syntax still valid).
- For code, run a linter/compiler/test where possible to catch issues introduced by the edit (e.g., a `sed` substitution that broke a string literal or regex elsewhere).
- If a sequence of edits is being made to the same file, verify incrementally rather than batching many unverified edits together.

## 7. When Creating New Files
- Use a dedicated file-creation tool/operation rather than piping content through `sed` or shell redirection tricks, when one is available.
- Follow the project's existing structure and naming conventions for where new files go.
- Don't create a new file when an existing one should simply be edited — check first.

## 8. Honesty About Edit Results
- If an edit tool reports an error (e.g., "string not found," "multiple matches"), don't retry blindly — re-view the file to understand why, then adjust.
- Don't claim a modification was applied unless the tool confirmed success and (where possible) the result was verified.
- If a `sed` command's exact effect is uncertain, state the assumption and verify by inspecting the file afterward.
