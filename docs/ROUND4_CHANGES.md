# This round: admin 403 root cause, LLM config in Settings, delete files in workspace

## 1 & 2. User management 403 / Settings invisible — same root cause

Every admin check in the app — `require_admin()` in `security.py`, ~26 inline
`caller != "admin"` checks in `main.py`, and the frontend's
`isAdmin = authedUser === "admin"` — compared the **username** against the
literal string `"admin"`. But first-boot setup (`POST /api/system/setup`)
always let you pick **any** username for that first account, correctly
storing `role = 'admin'` in the database. If you didn't literally name that
account `admin`, every admin feature 403'd — the role was right, nothing
ever read it.

- **`main.py`**: added `_get_user_role()` / `_is_admin()` (real DB lookup)
  and rewired `_require_admin()` plus every one of those ~26 checks to use
  them instead of the string comparison. `/api/auth/login` and
  `/api/auth/me` now return `role` / `is_admin` in the response, since
  nothing exposed it before.
- **`PUT /api/auth/users/{username}`** now accepts a `role` field too
  (admin-only), so an admin can actually **promote another account** —
  there was no way to do this before, ever, regardless of the bug, since
  the endpoint only touched password/is_active. Includes a guard against
  demoting the last remaining admin.
- The "cannot deactivate/delete admin" guards on user management were also
  generalized from `username == "admin"` to "has the admin role" — same
  fix, applied to safety checks instead of permission checks.
- **`App.tsx`**: `isAdmin` is now real state, set from the login response
  and persisted in session storage (`storeAuth` takes an `isAdmin` param
  now), not derived from the username. `SettingsPanel` receives it as a
  prop instead of recomputing the same broken check internally. The user
  list's "ADMIN" badge and edit/delete gating now check `u.role`, not
  `u.username`.

**If you're not currently logged in as an account with `role='admin'` in the
database, this fix alone won't retroactively grant it** — you'd need to
either log in as whichever account first-boot setup created, or have that
account promote yours via the new `role` field on the update-user endpoint.

## 3. LLM base URL + API key in Settings

Added to the **⛭ SYSTEM** settings tab (`settings_store.py`):
- **`LLM_BASE_URL`** — override the endpoint (self-hosted gateway, proxy,
  custom Ollama host); blank = provider's built-in default.
- **`LLM_API_KEY`** — saved server-side, used for every LLM call from then
  on. This is the one deliberate exception to "no secrets in the settings
  store" — it's masked everywhere it leaves the server: `GET /api/settings`
  only ever returns `is_set: true/false` and the last 4 characters (e.g.
  `••••••••cdef`), never the full value. The input field is a password type
  that starts empty (never pre-filled with the mask) — typing a new value
  replaces the stored key; leaving it blank and saving leaves it untouched.
- `_build_llm_config()` in `main.py` now checks these settings first,
  falling back to the existing `{PROVIDER}_API_KEY` env var if nothing's
  set — fully backward compatible with your current `.env`.

## 4. Delete selected files in the workspace

The file browser already had checkboxes (for "attach to next message") but
no way to remove files. Added:
- **`DELETE /api/workspace/{user_id}/files`** — body `{project, paths[]}`,
  same realpath-guard traversal protection as every other workspace
  endpoint, reports per-file success/failure rather than aborting a whole
  batch on one bad path, capped at 200 paths per call.
- **`App.tsx`**: a **🗑 DELETE** button appears next to the selection count
  whenever files are checked, with an inline CONFIRM/CANCEL step (no
  accidental deletes), refreshes the file tree on completion, and surfaces
  any per-file failures.

## Verification
`py_compile` clean on every changed `.py` file; `tsc --noEmit` clean on
`App.tsx` (only expected missing-type noise from checking outside the real
project's `tsconfig`). Manually exercised the masked-secret round trip
(save → masked read → clear → validation errors on bad URL / short key) and
confirmed `get_setting()` still returns the real unmasked value server-side.
