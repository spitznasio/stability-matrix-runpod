# Server Admin: Environment Variable Management — Design Spec

## Problem

Changing any of this app's env vars (API tokens, service credentials, cache
tuning, CUDA settings, etc.) today requires editing the RunPod template/pod
config and recreating the pod. Server Admin (`server_admin/`, port 8001)
already gives operational control over the other services — it should also
let an operator view and edit the variables that configure them, without a
full pod recreation.

## Non-goals

- Not a general env var editor: only a curated, known set of app-relevant
  variables (plus CUDA-related ones) is shown — no adding arbitrary new keys.
- Does not fix the pre-existing `aria2-rpc` `--rpc-secret` baked-at-import-time
  behavior in `server_admin/supervisor.py` (`SERVICES` dict is built once at
  module import). `ARIA2_RPC_SECRET` is treated as pod-restart-only in this
  feature; `supervisor.py` is untouched.
- Does not attempt to make Server Admin restart itself. Its own vars
  (`SERVER_ADMIN_*`) always require a full pod restart to apply, since Server
  Admin isn't a supervised service.
- Does not add an edit history/audit log.

## Architecture

### Registry (`server_admin/env_vars.py`, new module)

A static list of `EnvVarSpec` entries, one per curated variable:

```python
@dataclass(frozen=True)
class EnvVarSpec:
    key: str
    category: str          # e.g. "CivitAI Manager", "Server Admin", "GPU / CUDA"
    description: str
    sensitive: bool = False
    default: str | None = None       # shown as placeholder when unset
    owner_service: str | None = None # SERVICES key to restart, or None = pod-restart-only
```

Curated set (derived from `CLAUDE.md`'s env var table plus a read-through of
each app's `config.py`, plus CUDA vars):

- **CivitAI Manager** (`owner_service="civitai-manager"`): `CIVITAI_API_TOKEN`
  (sensitive), `CIVITAI_BASE_URL`, `INVOKEAI_BASE_URL`,
  `CIVITAI_MANAGER_LOG_LEVEL`, `CIVITAI_MANAGER_USERNAME`,
  `CIVITAI_MANAGER_PASSWORD` (sensitive), `CIVITAI_MANAGER_SESSION_SECRET`
  (sensitive), `CIVITAI_CACHE_TTL_SECONDS`, `CIVITAI_CACHE_MAXSIZE`,
  `CIVITAI_DOWNLOAD_DIR`, `CIVITAI_METADATA_DIR`, `ARIA2_RPC_URL`.
- **aria2 / downloads**: `ARIA2_RPC_SECRET` (sensitive, `owner_service=None`
  per Non-goals above).
- **InvokeAI / CUDA** (`owner_service="invokeai"`): `PYTORCH_CUDA_ALLOC_CONF`,
  `CUDA_CACHE_MAXSIZE`, `CUDA_VISIBLE_DEVICES`, `HF_HUB_ENABLE_HF_TRANSFER`.
- **OneDrive Sync Manager** (`owner_service="onedrive-sync"`):
  `ONEDRIVE_MANAGER_USERNAME`, `ONEDRIVE_MANAGER_PASSWORD_HASH` (sensitive),
  `ONEDRIVE_MANAGER_SESSION_SECRET` (sensitive), `ONEDRIVE_CLIENT_ID`,
  `ONEDRIVE_TENANT_ID`, `ONEDRIVE_SCOPES`, `ONEDRIVE_SYNC_LOCAL_BASE_ROOT`,
  `ONEDRIVE_SYNC_MAX_RETRIES`, `ONEDRIVE_SYNC_JOB_HISTORY_MAX_JOBS`,
  `ONEDRIVE_SYNC_JOB_MAX_EVENTS`, `ONEDRIVE_MANAGER_LOG_LEVEL`.
- **Server Admin** (`owner_service=None`): `SERVER_ADMIN_USERNAME`,
  `SERVER_ADMIN_PASSWORD` (sensitive), `SERVER_ADMIN_SESSION_SECRET`
  (sensitive), `SERVER_ADMIN_AUTO_RESTART`,
  `SERVER_ADMIN_CRASH_MONITOR_INTERVAL_S`, `SERVER_ADMIN_MAX_LOG_TAIL_LINES`.

### Persistence & propagation

- New file `/workspace/server-admin/env-overrides.env` (`KEY=value` lines,
  values `shlex.quote`d, written atomically via temp file + `os.replace`).
  Distinct from the existing ephemeral `/tmp/server-admin/` supervisor state
  dir — this one must survive pod restarts, so it lives on the volume disk.
- `start.sh` sources it (`set -a; source ...; set +a`) as the first thing it
  does (before reading `CIVITAI_API_TOKEN` or generating
  `ARIA2_RPC_SECRET`), so saved overrides win over the pod's originally
  injected values and are visible to every subsequently launched process.
- On save, `env_vars.py` also live-applies the new value to the running
  Server Admin process's `os.environ` immediately. Because
  `ManagedService.start()` builds each subprocess's environment as
  `{**os.environ, **spec.env_overrides}`, restarting the owning service
  through the existing supervisor picks up the new value right away — no
  pod restart needed for vars with an `owner_service`.
- At Server Admin startup, snapshot `os.environ` once into an in-memory
  `_original_env` dict (before any overrides file is applied to the running
  process — read the file's raw content for display/edit purposes, but the
  snapshot reflects only what the container actually injected). "Clear
  override" removes the var's line from the file and resets `os.environ[key]`
  back to `_original_env.get(key)` (or deletes it if it wasn't originally
  set).
- Vars with `owner_service=None` show a static note instead of a restart
  button: "Applies after the next full pod restart."

### Routes (`server_admin/main.py`)

- `GET /environment` — page shell (nav item "Environment", new SVG icon in
  `base.html`'s sidebar, following the existing Dashboard/Services/Logs
  pattern).
- `GET /environment/list` — htmx partial, table grouped by `category`, one
  row per `EnvVarSpec`. Sensitive values render masked (e.g. `••••1234`,
  last 4 chars) and are **never** sent in full in this response.
- `GET /environment/{key}/reveal` — returns the row partial with the real
  value visible. Same auth as everything else in this app (session cookie);
  no separate confirmation step, consistent with this app's existing
  single-tier auth model.
- `GET /environment/{key}/edit` — htmx partial, inline edit form (text input
  pre-filled with current real value, Save/Cancel).
- `POST /environment/{key}` — validates `key` is in the registry, writes the
  override file, live-applies to `os.environ`, returns the updated row
  (masked again if sensitive) plus the restart-button/pod-restart-note.
- `POST /environment/{key}/clear` — removes the override, resets
  `os.environ`, returns the updated row.
- Restart action reuses the existing `POST /services/{key}/restart` — no new
  endpoint needed there.

### UI

Table per category (styled like `_services_list.html`): Name | Description |
Value | Actions. Clicking "Edit" swaps the value cell for an inline form.
After Save, the row shows the new (masked-if-sensitive) value and, if
`owner_service` is set, a "Restart `<service>` to apply" button; otherwise
static text "Applies after next full pod restart."

## Error Handling

- `POST /environment/{key}` for an unknown key → 404 via the existing
  `render_error` helper, same pattern as `/services/{key}/{action}`.
- Override file write failures (e.g. `/workspace` not mounted) surface as a
  rendered error partial; `os.environ` is still updated in-memory so the
  live-apply half of the feature keeps working even if persistence fails.
- Malformed/corrupt override file on boot (manual edits, truncation): `start.sh`
  sourcing a broken file would break container startup, so the file format is
  kept deliberately simple (`KEY=value`, one per line, always written by this
  feature's own code, never hand-edited in the intended workflow).

## Testing

No existing test suite in this repo (per `AGENTS.md`); validation is manual —
run Server Admin locally, exercise view/reveal/edit/clear for a sensitive and
non-sensitive var, confirm the override file contents, confirm restarting the
owning service reflects the new value (e.g. change `CIVITAI_MANAGER_LOG_LEVEL`
and check the next log lines' verbosity).
