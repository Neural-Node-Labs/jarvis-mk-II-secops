# File Management Directive for AI Agents

A set of principles and workflows for how an AI agent should create, read, edit,
and deliver files when operating in a sandboxed environment with shell access.

---

## 1. Core Principles

1. **Separate scratch space from delivery space.** Work happens in a private
   working directory; only finished outputs are placed where the user can see them.
2. **Never overwrite blindly.** File creation should fail if a file already
   exists, to avoid accidental data loss. Edits to existing files should be
   surgical (targeted replacements), not full rewrites, unless explicitly intended.
3. **Match the tool to the file size and type.** Small files: one-shot creation.
   Large files: iterative construction. Binary/structured formats (docx, xlsx,
   pptx, pdf): generate programmatically via scripts, not raw text dumps.
4. **Always verify before delivering.** Read back what was written, run code
   that was generated, check file sizes/structure before presenting to the user.
5. **Read-only zones stay read-only.** Reference material, uploaded files, and
   shared skill/template directories must be copied elsewhere before modification.

---

## 2. Directory Roles

| Directory | Purpose | Read/Write |
|---|---|---|
| `workspace/` (e.g. `/home/agent`) | Scratchpad for drafts, intermediate files, generated scripts | Read/Write |
| `uploads/` (e.g. `/mnt/user-data/uploads`) | Files the user provided | Read-only |
| `outputs/` (e.g. `/mnt/user-data/outputs`) | Final deliverables the user will see | Write (copy-in only) |
| `skills/` or `templates/` (e.g. `/mnt/skills`) | Reference guides, best-practice docs | Read-only |

**Rule of thumb:** create and iterate in `workspace/`, only copy the *final*
version of a file into `outputs/`, and never write directly into read-only zones.

---

## 3. File Creation Workflow

### Step 1 — Check for an applicable skill/template
Before writing any code or generating a file, check whether a best-practice
guide exists for that file type (e.g. a "docx skill", "pptx skill",
"spreadsheet skill"). These encode environment-specific constraints
(available libraries, rendering quirks, output conventions) that general
knowledge may get wrong. Read all plausibly relevant guides — multiple may
apply to one task.

### Step 2 — Decide: inline answer vs. file
Not everything needs to become a file. Use these signals:

- **Make a file** when the user asks to "save", "download", "create a file",
  names an extension/path, or the content is a standalone artifact (report,
  article, script, presentation, spreadsheet) the user will reuse outside the
  conversation — regardless of length.
- **Answer inline** when the content is a direct conversational response:
  summaries, explanations, brainstorms, short code snippets (<=20 lines),
  short lists, or anything the user explicitly asked to keep short.

### Step 3 — Choose the creation strategy by size

- **Short files (< 100 lines):** create the whole file in a single
  "create new file" operation, written directly to the workspace (or directly
  to outputs for trivial single-file tasks).
- **Long files (> 100 lines):** build iteratively:
  1. Write a skeleton/outline first (headings, function signatures, section
     stubs).
  2. Fill in sections one at a time using targeted edits.
  3. Review the assembled file.
  4. Refine as needed.
- **Very large or repetitive content:** generate via script/loop rather than
  manually authoring every line (e.g. a Python script that writes 500 rows of
  data, rather than typing 500 lines).
- **Binary/structured formats (docx, xlsx, pptx, pdf, images):** never hand-author
  the raw bytes. Write a generation script using the appropriate library, run
  it, and verify the resulting file opens/parses correctly.

### Step 4 — Verify
- For text/code: read the file back, confirm structure and correctness.
- For code: execute it (tests, linter, or a sample run) before declaring done.
- For generated documents: open/parse programmatically to confirm validity
  (e.g. check page count, sheet names, slide count).

### Step 5 — Deliver
- Copy the finished file from the workspace into the output/delivery directory.
- Present only the **file**, not the containing folder, to the user.
- Keep the post-delivery message brief — link/reference the file without
  re-explaining its entire contents.

---

## 4. File Editing Workflow

### Editing existing files (already on disk)
1. **View the current state first** — never edit blind, and never rely on a
   stale view from earlier in the conversation if prior edits have been made.
2. **Use targeted replacements** for changes: find a unique snippet of the
   old content and replace it with new content. This:
   - Preserves everything else in the file untouched.
   - Makes diffs reviewable.
   - Avoids accidentally dropping unrelated content during a full rewrite.
3. **One unique match per edit.** If the snippet to replace appears multiple
   times, expand the context until it's unique, or perform multiple distinct edits.
4. **Re-view after each edit** before making the next one — line numbers and
   surrounding context shift after a replacement.
5. **Full rewrite only when appropriate** — e.g. the file is small, the
   structure is changing wholesale, or targeted edits would be more error-prone
   than rewriting.

### Editing code specifically
- Make one logical change at a time (one function, one bug fix, one feature).
- After editing, run the code (or relevant tests) to confirm it still works.
- Preserve existing code style/conventions unless asked to change them.
- For multi-file changes, edit and verify one file before moving to the next,
  especially if files depend on each other.

### Read-only sources
- If a file that needs modification lives in a read-only location (uploads,
  templates, shared references), **copy it to the workspace first**, then edit
  the copy.

---

## 5. Handling Different File Types

| Type | Approach |
|---|---|
| Plain text / Markdown / config (.txt, .md, .json, .yaml) | Direct create/edit; small enough for one-shot or simple targeted edits |
| Source code (.py, .js, .ts, etc.) | Create skeleton -> implement -> test/run -> refine |
| CSV/data files | Generate via script (e.g. pandas) for correctness with large/structured data |
| Word documents (.docx) | Use a docx-generation library; follow formatting best-practice guide |
| Spreadsheets (.xlsx) | Use a spreadsheet library; verify formulas/data with a read-back |
| Presentations (.pptx) | Use a presentation library; build slide-by-slide, verify slide count/content |
| PDFs | Generate via PDF library or convert from another format; verify page count/text extraction |
| Images | Only manipulate via tools/libraries (e.g. Pillow); never hand-edit binary |

---

## 6. Safety & Hygiene Checklist

Before finishing any file-related task, confirm:

- [ ] No read-only file or directory was modified or deleted.
- [ ] No existing file was silently overwritten unless that was the intent.
- [ ] Intermediate/scratch files that aren't part of the deliverable are
      cleaned up or left only in the workspace (not the delivery directory).
- [ ] The final file(s) were verified (opened, parsed, or executed successfully).
- [ ] Only the relevant final file(s) -- not entire folders -- are presented
      to the user.
- [ ] File and directory names follow sensible, descriptive conventions
      (no spaces/special characters where they'd cause issues).

---

## 7. Summary Workflow (text diagram)

```
User request
   |
   v
Need a file? --No--> Answer inline
   | Yes
   v
Relevant skill/template exists? --Yes--> Read it first
   |
   v
Size/type?
   - Small text/code      -> Create in one shot (workspace)
   - Large text/code       -> Outline -> iterative section edits (workspace)
   - Existing file          -> View -> targeted edits -> re-view
   - Binary/structured      -> Write generator script -> run -> verify
   |
   v
Verify (read back / run / parse)
   |
   v
Copy final file(s) to output/delivery directory
   |
   v
Present file(s) to user with brief summary
```
