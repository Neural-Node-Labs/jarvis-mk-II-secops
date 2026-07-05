# Settings-editable config + workspace-directory display fix

## The actual bug
`main.py` had **two duplicate copies** of the workspace-path helpers, and both
computed `WORKSPACE_ROOT = os.getenv("JARVIS_WORKSPACE_ROOT", ...)` **once, at
import time**, as a module constant. Several FastAPI route signatures also
used `project: str = DEFAULT_PROJECT` — a constant baked into the function's
default argument at *definition* time. Net effect: even if you set
`JARVIS_WORKSPACE_ROOT` correctly, changing it required a full process
restart to take effect anywhere.

Separately, in `App.tsx`, the workspace path shown to the user was **never
fetched from the backend at all** — three separate places rendered a
hardcoded `const _base_workspace = "/app/workspace"` guess (and one of them,
the input-bar badge's tooltip, guessed a *different* wrong prefix,
`/app/data/...`). The backend's `/api/workspace/{user_id}` response didn't
even include the real path, so there was nothing to fetch. That's "workspace
not showing the correct directory."

## Fix
- **`core/settings_store.py`** (new) — JSON-file-backed settings
  (`./data/settings.json` by default), read fresh on every call (no cached
  constant), with precedence `override → env var → default`. Registry of
  6 editable keys: `JARVIS_WORKSPACE_ROOT`, `JARVIS_DEFAULT_PROJECT`,
  `JARVIS_MAX_PARALLEL`, `JARVIS_TASK_TIMEOUT`, `LLM_PROVIDER`, `LLM_MODEL`.
  Each has a validator (e.g. workspace root must be an absolute path that
  can actually be created). **Deliberately excludes API keys/secrets** —
  those stay in the environment.
- **`main.py`**:
  - Removed the duplicate workspace-helper block; the survivors
    (`_workspace_root()`, `_default_project()`, `_safe_name()`, `_user_root()`,
    `_workspace_path()`, `_is_text_file()`) are now **functions**, called
    fresh every request, backed by `settings_store.get_setting(...)`.
  - Fixed every route that defaulted a `project` param to the old frozen
    constant.
  - `/api/workspace/{user_id}` and `/api/projects/{user_id}` now actually
    return `workspace_root` in the response body.
  - New endpoints: `GET /api/settings`, `POST /api/settings`,
    `POST /api/settings/reset` — admin-only, this is the "editable in
    Settings, not .env" surface.
  - `JARVIS_MAX_PARALLEL`/`JARVIS_TASK_TIMEOUT`/`LLM_PROVIDER`/`LLM_MODEL`
    lookups now go through the same live settings store.
- **`code_tools_skill.py`** — same fix applied to the coding-agent tools
  from the previous round: `WORKSPACE_ROOT` constant → `_workspace_root()`
  function backed by the same store, so an operator's edit takes effect for
  the Search→Edit→Validate loop immediately too.
- **`App.tsx`**:
  - `WorkspaceSidebar` already fetched the real root into a `wsRoot` state
    variable — it just wasn't displaying it. Both spots now show `wsRoot`
    (falling back to the old guess only before the fetch resolves).
  - Added a `workspaceRoot` state at the top level of `App`, fetched from
    `/api/workspace/{userId}` right after login (and whenever the project
    changes), used in the welcome-screen line and the input-bar badge —
    fixing both the stale value and the `/app/data` vs `/app/workspace`
    inconsistency between them.
  - New **⛭ SYSTEM** tab in Settings (admin-only, since the API is
    admin-only): lists each editable setting with its current value, source
    badge (CUSTOM / FROM ENV / DEFAULT), an input to change it, and a RESET
    button per overridden key. Saving calls `POST /api/settings` and — if
    the workspace root changed — immediately re-fetches it so the header
    badge updates without a page reload.

## Using it
```bash
# View current effective settings
curl -H "Authorization: Bearer <admin_token>" http://localhost:8000/api/settings

# Change the workspace root at runtime, no restart
curl -X POST http://localhost:8000/api/settings \
  -H "Authorization: Bearer <admin_token>" -H "Content-Type: application/json" \
  -d '{"JARVIS_WORKSPACE_ROOT": "/data/jarvis_workspace"}'

# Revert to env/default
curl -X POST http://localhost:8000/api/settings/reset \
  -H "Authorization: Bearer <admin_token>" -H "Content-Type: application/json" \
  -d '{"keys": ["JARVIS_WORKSPACE_ROOT"]}'
```
Or just open Settings → **⛭ SYSTEM** in the UI as an admin.

## Note
`settings.json` is stored at `./data/settings.json` (configurable via
`JARVIS_SETTINGS_PATH`). If you run multiple replicas of the backend behind a
load balancer, this file is per-instance — put it on a shared volume, or
treat it as single-instance config for now.
