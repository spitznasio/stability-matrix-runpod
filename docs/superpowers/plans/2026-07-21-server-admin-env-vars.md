# Server Admin: Environment Variable Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an "Environment" page to the Server Admin dashboard (port 8001) that lets an operator view and edit a curated set of this app's env vars, with changes persisted to the volume disk and live-applied where possible.

**Architecture:** A new pure-logic module (`server_admin/env_vars.py`) holds a static registry of `EnvVarSpec` entries and the read/write logic for a `KEY=value` override file under `/workspace/server-admin/`. New FastAPI routes in `server_admin/main.py` render/edit rows via htmx partial swaps, following the existing dashboard/services page pattern exactly. `start.sh` sources the override file first thing so persisted edits survive a pod restart.

**Tech Stack:** FastAPI, Jinja2, htmx (already in use throughout `server_admin/`) — no new dependencies.

## Global Constraints

- Only the curated registry in `env_vars.py` is editable — no arbitrary new env var keys can be added through the UI (per spec's Non-goals).
- Sensitive values (passwords, tokens, secrets) are never sent to the browser unmasked except via an explicit reveal action.
- `server_admin/supervisor.py` is **not modified** — `ARIA2_RPC_SECRET` is treated as pod-restart-only rather than fixing its baked-at-import-time behavior (per spec's Non-goals; user explicitly deprioritized this).
- `SERVER_ADMIN_*` vars are always pod-restart-only (`owner_service=None`) — Server Admin isn't a supervised service and can't restart itself.
- The restart action reuses the existing `POST /services/{key}/restart` endpoint — no new supervisor-facing endpoint.
- This repo has no test suite (confirmed: only `.venv/` dependency packages contain `test_*.py` files). Every task is verified manually — running the module standalone, running the dev server and using curl/browser, or a bash dry-run — matching this repo's existing "Docker build success and runtime sanity" validation style (`AGENTS.md`).
- Override file format is deliberately simple (`KEY=value`, `shlex.quote`d, one per line) since `start.sh` sources it directly — no YAML/JSON.

---

### Task 1: Curated registry and override-file logic (`server_admin/env_vars.py`)

**Files:**
- Create: `server_admin/env_vars.py`

**Interfaces:**
- Produces (used by Task 2): `EnvVarSpec` dataclass (fields: `key: str`, `category: str`, `description: str`, `sensitive: bool = False`, `default: str | None = None`, `owner_service: str | None = None`); `REGISTRY: list[EnvVarSpec]`; `get_spec(key: str) -> EnvVarSpec` (raises `KeyError` for unknown keys); `categories() -> list[tuple[str, list[EnvVarSpec]]]`; `current_value(key: str) -> str | None`; `has_override(key: str) -> bool`; `mask(value: str | None) -> str`; `set_value(key: str, value: str) -> None` (raises `KeyError` for unknown keys; live-applies to `os.environ` first, then raises `OSError` if writing `OVERRIDES_FILE` fails — the live value still takes effect even then); `clear_value(key: str) -> None` (same `KeyError`/`OSError` behavior as `set_value`).
- Module-level `OVERRIDES_DIR: Path` and `OVERRIDES_FILE: Path` (`/workspace/server-admin` and `/workspace/server-admin/env-overrides.env`) — Task 3 (`start.sh`) must source the same literal path.

- [ ] **Step 1: Write `server_admin/env_vars.py`**

```python
"""Curated registry of the env vars Server Admin's Environment page can show
and edit, plus the read/write logic for persisting edits to
/workspace/server-admin/env-overrides.env (sourced by start.sh on boot, so
edits survive a pod restart) and live-applying them to this process's
os.environ (so a supervised service restart picks them up immediately).
"""

import os
import shlex
from dataclasses import dataclass
from pathlib import Path

# SERVER_ADMIN_ENV_OVERRIDES_DIR lets this be pointed at a writable dir
# during local development, where /workspace doesn't exist — same pattern as
# supervisor.py's SERVER_ADMIN_STATE_DIR. Left unset in a real pod, where
# /workspace is the volume disk.
OVERRIDES_DIR = Path(os.environ.get("SERVER_ADMIN_ENV_OVERRIDES_DIR", "/workspace/server-admin"))
OVERRIDES_FILE = OVERRIDES_DIR / "env-overrides.env"

# Snapshot of the environment as this process actually started with, used by
# clear_value() to revert an override back to whatever the container had
# before this module made any edits of its own.
_ORIGINAL_ENV: dict[str, str] = dict(os.environ)


@dataclass(frozen=True)
class EnvVarSpec:
    key: str
    category: str
    description: str
    sensitive: bool = False
    default: str | None = None
    # SERVICES key (server_admin.supervisor) to restart to apply an edit, or
    # None if this var can only take effect after a full pod restart (either
    # it belongs to Server Admin itself, which isn't a supervised service, or
    # the owning process reads it at a point a supervised restart can't
    # reach — see ARIA2_RPC_SECRET below).
    owner_service: str | None = None


REGISTRY: list[EnvVarSpec] = [
    # --- CivitAI Manager ---
    EnvVarSpec(
        key="CIVITAI_API_TOKEN",
        category="CivitAI Manager",
        description="CivitAI API token used for authenticated downloads and searches.",
        sensitive=True,
        owner_service="civitai-manager",
    ),
    EnvVarSpec(
        key="CIVITAI_BASE_URL",
        category="CivitAI Manager",
        description="Base URL for the CivitAI API.",
        default="https://civitai.com/api/v1",
        owner_service="civitai-manager",
    ),
    EnvVarSpec(
        key="INVOKEAI_BASE_URL",
        category="CivitAI Manager",
        description="Base URL CivitAI Manager uses to reach InvokeAI's API.",
        default="http://localhost:9090",
        owner_service="civitai-manager",
    ),
    EnvVarSpec(
        key="CIVITAI_MANAGER_LOG_LEVEL",
        category="CivitAI Manager",
        description="Log verbosity for CivitAI Manager's own log messages.",
        default="INFO",
        owner_service="civitai-manager",
    ),
    EnvVarSpec(
        key="CIVITAI_MANAGER_USERNAME",
        category="CivitAI Manager",
        description="Login username for the CivitAI Manager UI. Unset either this or the password to disable login.",
        owner_service="civitai-manager",
    ),
    EnvVarSpec(
        key="CIVITAI_MANAGER_PASSWORD",
        category="CivitAI Manager",
        description="Login password for the CivitAI Manager UI.",
        sensitive=True,
        owner_service="civitai-manager",
    ),
    EnvVarSpec(
        key="CIVITAI_MANAGER_SESSION_SECRET",
        category="CivitAI Manager",
        description="Signs the CivitAI Manager session cookie. Changing it logs out all active sessions.",
        sensitive=True,
        owner_service="civitai-manager",
    ),
    EnvVarSpec(
        key="CIVITAI_CACHE_TTL_SECONDS",
        category="CivitAI Manager",
        description="How long CivitAI API responses are cached, in seconds.",
        default="3600",
        owner_service="civitai-manager",
    ),
    EnvVarSpec(
        key="CIVITAI_CACHE_MAXSIZE",
        category="CivitAI Manager",
        description="Maximum number of cached CivitAI API responses.",
        default="500",
        owner_service="civitai-manager",
    ),
    EnvVarSpec(
        key="CIVITAI_DOWNLOAD_DIR",
        category="CivitAI Manager",
        description='Destination folder for the "Download to folder" aria2 path.',
        default="/workspace/civitai-downloads",
        owner_service="civitai-manager",
    ),
    EnvVarSpec(
        key="CIVITAI_METADATA_DIR",
        category="CivitAI Manager",
        description="Where Installed-page CivitAI metadata sidecars are written.",
        default="/workspace/civitai-metadata",
        owner_service="civitai-manager",
    ),
    EnvVarSpec(
        key="ARIA2_RPC_URL",
        category="CivitAI Manager",
        description="URL CivitAI Manager uses to reach the local aria2 RPC daemon.",
        default="http://127.0.0.1:6800/jsonrpc",
        owner_service="civitai-manager",
    ),
    # --- aria2 / Downloads ---
    EnvVarSpec(
        key="ARIA2_RPC_SECRET",
        category="aria2 / Downloads",
        description=(
            "Shared secret between the aria2 RPC daemon and CivitAI Manager. "
            "The aria2 daemon only reads this at its own process start "
            "(baked into its launch command), so restarting it here would "
            "not actually pick up a new value — requires a full pod restart."
        ),
        sensitive=True,
        owner_service=None,
    ),
    # --- InvokeAI / CUDA ---
    EnvVarSpec(
        key="PYTORCH_CUDA_ALLOC_CONF",
        category="InvokeAI / CUDA",
        description="PyTorch CUDA allocator tuning, e.g. max_split_size_mb:512,expandable_segments:True to work around OOM during tiling.",
        default="backend:cudaMallocAsync",
        owner_service="invokeai",
    ),
    EnvVarSpec(
        key="CUDA_CACHE_MAXSIZE",
        category="InvokeAI / CUDA",
        description="Size of the CUDA shader cache, in bytes.",
        default="4294967296",
        owner_service="invokeai",
    ),
    EnvVarSpec(
        key="CUDA_VISIBLE_DEVICES",
        category="InvokeAI / CUDA",
        description='Restricts which GPUs are visible to InvokeAI, e.g. "0" on a multi-GPU pod.',
        owner_service="invokeai",
    ),
    EnvVarSpec(
        key="HF_HUB_ENABLE_HF_TRANSFER",
        category="InvokeAI / CUDA",
        description="Enables the Rust-based fast transfer backend for HuggingFace downloads.",
        owner_service="invokeai",
    ),
    # --- OneDrive Sync Manager ---
    EnvVarSpec(
        key="ONEDRIVE_MANAGER_USERNAME",
        category="OneDrive Sync Manager",
        description="Login username for the OneDrive Sync Manager UI.",
        owner_service="onedrive-sync",
    ),
    EnvVarSpec(
        key="ONEDRIVE_MANAGER_PASSWORD_HASH",
        category="OneDrive Sync Manager",
        description="Hashed login password for the OneDrive Sync Manager UI.",
        sensitive=True,
        owner_service="onedrive-sync",
    ),
    EnvVarSpec(
        key="ONEDRIVE_MANAGER_SESSION_SECRET",
        category="OneDrive Sync Manager",
        description="Signs the OneDrive Sync Manager session cookie. Changing it logs out all active sessions.",
        sensitive=True,
        owner_service="onedrive-sync",
    ),
    EnvVarSpec(
        key="ONEDRIVE_CLIENT_ID",
        category="OneDrive Sync Manager",
        description="Azure AD application (client) ID used for OneDrive OAuth.",
        owner_service="onedrive-sync",
    ),
    EnvVarSpec(
        key="ONEDRIVE_TENANT_ID",
        category="OneDrive Sync Manager",
        description="Azure AD tenant ID used for OneDrive OAuth.",
        default="common",
        owner_service="onedrive-sync",
    ),
    EnvVarSpec(
        key="ONEDRIVE_SCOPES",
        category="OneDrive Sync Manager",
        description="Space-separated OAuth scopes requested from Microsoft Graph.",
        owner_service="onedrive-sync",
    ),
    EnvVarSpec(
        key="ONEDRIVE_SYNC_LOCAL_BASE_ROOT",
        category="OneDrive Sync Manager",
        description="Local base directory sync jobs are rooted under.",
        default="/workspace",
        owner_service="onedrive-sync",
    ),
    EnvVarSpec(
        key="ONEDRIVE_SYNC_MAX_RETRIES",
        category="OneDrive Sync Manager",
        description="Maximum retry attempts for a failed sync job.",
        default="3",
        owner_service="onedrive-sync",
    ),
    EnvVarSpec(
        key="ONEDRIVE_SYNC_JOB_HISTORY_MAX_JOBS",
        category="OneDrive Sync Manager",
        description="Maximum number of past sync jobs kept in history.",
        default="250",
        owner_service="onedrive-sync",
    ),
    EnvVarSpec(
        key="ONEDRIVE_SYNC_JOB_MAX_EVENTS",
        category="OneDrive Sync Manager",
        description="Maximum number of events kept per sync job.",
        default="200",
        owner_service="onedrive-sync",
    ),
    EnvVarSpec(
        key="ONEDRIVE_MANAGER_LOG_LEVEL",
        category="OneDrive Sync Manager",
        description="Log verbosity for OneDrive Sync Manager's own log messages.",
        default="INFO",
        owner_service="onedrive-sync",
    ),
    # --- Server Admin ---
    EnvVarSpec(
        key="SERVER_ADMIN_USERNAME",
        category="Server Admin",
        description="Login username for this dashboard. Unset either this or the password to disable login.",
        owner_service=None,
    ),
    EnvVarSpec(
        key="SERVER_ADMIN_PASSWORD",
        category="Server Admin",
        description="Login password for this dashboard.",
        sensitive=True,
        owner_service=None,
    ),
    EnvVarSpec(
        key="SERVER_ADMIN_SESSION_SECRET",
        category="Server Admin",
        description="Signs this dashboard's session cookie. Changing it logs out all active sessions.",
        sensitive=True,
        owner_service=None,
    ),
    EnvVarSpec(
        key="SERVER_ADMIN_AUTO_RESTART",
        category="Server Admin",
        description="Comma-separated service keys to auto-restart when they crash, e.g. invokeai,aria2-rpc.",
        owner_service=None,
    ),
    EnvVarSpec(
        key="SERVER_ADMIN_CRASH_MONITOR_INTERVAL_S",
        category="Server Admin",
        description="How often, in seconds, the background monitor checks for crashed services.",
        default="10",
        owner_service=None,
    ),
    EnvVarSpec(
        key="SERVER_ADMIN_MAX_LOG_TAIL_LINES",
        category="Server Admin",
        description="Upper bound on the lines query param for the Logs page.",
        default="5000",
        owner_service=None,
    ),
]

_BY_KEY: dict[str, EnvVarSpec] = {spec.key: spec for spec in REGISTRY}


def get_spec(key: str) -> EnvVarSpec:
    return _BY_KEY[key]


def categories() -> list[tuple[str, list[EnvVarSpec]]]:
    """REGISTRY grouped by category, preserving registry order."""
    grouped: dict[str, list[EnvVarSpec]] = {}
    for spec in REGISTRY:
        grouped.setdefault(spec.category, []).append(spec)
    return list(grouped.items())


def current_value(key: str) -> str | None:
    return os.environ.get(key)


def mask(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "•" * len(value)
    return "•" * (len(value) - 4) + value[-4:]


def _read_overrides() -> dict[str, str]:
    if not OVERRIDES_FILE.exists():
        return {}
    overrides: dict[str, str] = {}
    for line in OVERRIDES_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw_value = line.partition("=")
        parts = shlex.split(raw_value)
        overrides[key] = parts[0] if parts else ""
    return overrides


def _write_overrides(overrides: dict[str, str]) -> None:
    OVERRIDES_DIR.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={shlex.quote(value)}" for key, value in sorted(overrides.items())]
    content = "\n".join(lines) + ("\n" if lines else "")
    tmp_path = OVERRIDES_FILE.with_suffix(".tmp")
    tmp_path.write_text(content)
    tmp_path.replace(OVERRIDES_FILE)


def has_override(key: str) -> bool:
    return key in _read_overrides()


def set_value(key: str, value: str) -> None:
    """Raises KeyError for an unknown key. Live-applies to os.environ before
    attempting to persist, so a persistence failure (e.g. /workspace not
    mounted) still leaves the in-memory value updated — an OSError from this
    function means "applied live, but not saved to disk", not "not applied".
    """
    if key not in _BY_KEY:
        raise KeyError(key)
    os.environ[key] = value
    overrides = _read_overrides()
    overrides[key] = value
    _write_overrides(overrides)


def clear_value(key: str) -> None:
    """Raises KeyError for an unknown key. Same live-apply-before-persist
    ordering as set_value() — see its docstring.
    """
    if key not in _BY_KEY:
        raise KeyError(key)
    original = _ORIGINAL_ENV.get(key)
    if original is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = original
    overrides = _read_overrides()
    overrides.pop(key, None)
    _write_overrides(overrides)
```

- [ ] **Step 2: Manual verification — registry sanity**

Run:

```bash
cd /Users/thomasspitznas/Projects/runpod-stability-matrix
python3 -c "
from server_admin import env_vars
keys = [s.key for s in env_vars.REGISTRY]
assert len(keys) == len(set(keys)), 'duplicate key in REGISTRY'
assert env_vars.get_spec('CIVITAI_API_TOKEN').sensitive is True
assert env_vars.get_spec('CIVITAI_BASE_URL').owner_service == 'civitai-manager'
assert env_vars.get_spec('ARIA2_RPC_SECRET').owner_service is None
assert env_vars.get_spec('SERVER_ADMIN_USERNAME').owner_service is None
cats = dict(env_vars.categories())
assert set(cats) == {'CivitAI Manager', 'aria2 / Downloads', 'InvokeAI / CUDA', 'OneDrive Sync Manager', 'Server Admin'}
print('registry OK:', len(keys), 'vars across', len(cats), 'categories')
"
```

Expected: `registry OK: 30 vars across 5 categories` (no assertion errors).

- [ ] **Step 3: Manual verification — mask()**

Run:

```bash
python3 -c "
from server_admin import env_vars
assert env_vars.mask(None) == ''
assert env_vars.mask('') == ''
assert env_vars.mask('abc') == '•••'
assert env_vars.mask('supersecrettoken1234') == '••••••••••••••••1234'
print('mask OK')
"
```

Expected: `mask OK`.

- [ ] **Step 4: Manual verification — override file read/write/set/clear round-trip, in an isolated temp dir**

```bash
python3 -c "
import os, tempfile
from pathlib import Path

with tempfile.TemporaryDirectory() as tmp:
    from server_admin import env_vars
    env_vars.OVERRIDES_DIR = Path(tmp) / 'server-admin'
    env_vars.OVERRIDES_FILE = env_vars.OVERRIDES_DIR / 'env-overrides.env'
    env_vars._ORIGINAL_ENV = dict(os.environ)  # simulate boot snapshot

    key = 'CIVITAI_MANAGER_LOG_LEVEL'
    assert env_vars.has_override(key) is False
    assert env_vars.current_value(key) is None

    env_vars.set_value(key, 'DEBUG')
    assert env_vars.current_value(key) == 'DEBUG'
    assert env_vars.has_override(key) is True
    assert env_vars.OVERRIDES_FILE.read_text().strip() == 'CIVITAI_MANAGER_LOG_LEVEL=DEBUG'

    # value with a space must round-trip through shlex quoting
    env_vars.set_value('CIVITAI_BASE_URL', 'https://example.com/needs quoting')
    reread = env_vars._read_overrides()
    assert reread['CIVITAI_BASE_URL'] == 'https://example.com/needs quoting', reread

    env_vars.clear_value(key)
    assert env_vars.has_override(key) is False
    assert env_vars.current_value(key) is None

    try:
        env_vars.set_value('NOT_A_REAL_KEY', 'x')
        raise AssertionError('expected KeyError')
    except KeyError:
        pass

    print('override round-trip OK')
"
```

Expected: `override round-trip OK` (no assertion errors, no traceback).

- [ ] **Step 5: Manual verification — persistence failure still live-applies**

This is the scenario that actually occurs on any machine without `/workspace` (including this dev machine) — confirm `set_value()`/`clear_value()` update `os.environ` even when the file write fails:

```bash
python3 -c "
import os
from pathlib import Path
from server_admin import env_vars

env_vars.OVERRIDES_DIR = Path('/workspace/server-admin')  # doesn't exist / not writable here
env_vars.OVERRIDES_FILE = env_vars.OVERRIDES_DIR / 'env-overrides.env'
env_vars._ORIGINAL_ENV = dict(os.environ)

key = 'CIVITAI_MANAGER_LOG_LEVEL'
try:
    env_vars.set_value(key, 'DEBUG')
    raise AssertionError('expected an OSError from the unwritable /workspace path')
except OSError:
    pass
assert env_vars.current_value(key) == 'DEBUG', 'os.environ should be updated even though persistence failed'
print('live-apply-survives-persistence-failure OK')
"
```

Expected: `live-apply-survives-persistence-failure OK`. (If `/workspace` happens to exist and be writable on the machine running this — e.g. inside an actual pod — this assertion will fail because there's no OSError to catch; that's fine, it means the happy path is being exercised instead. This step only matters as a regression check on a machine without `/workspace`.)

- [ ] **Step 6: Commit**

```bash
git add server_admin/env_vars.py
git commit -m "$(cat <<'EOF'
feat: add curated env var registry for Server Admin

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Routes, templates, nav entry, styling

**Files:**
- Modify: `server_admin/main.py`
- Create: `server_admin/templates/environment.html`
- Create: `server_admin/templates/_environment_list.html`
- Create: `server_admin/templates/_environment_row.html`
- Modify: `server_admin/templates/base.html`
- Modify: `server_admin/static/app.js`
- Modify: `server_admin/static/style.css`

**Interfaces:**
- Consumes (from Task 1): `env_vars.EnvVarSpec`, `env_vars.REGISTRY`, `env_vars.categories()`, `env_vars.current_value(key)`, `env_vars.has_override(key)`, `env_vars.mask(value)`, `env_vars.get_spec(key)`, `env_vars.set_value(key, value)`, `env_vars.clear_value(key)`.
- Produces: routes `GET /environment`, `GET /environment/list`, `GET /environment/{key}/view`, `GET /environment/{key}/reveal`, `GET /environment/{key}/edit`, `POST /environment/{key}`, `POST /environment/{key}/clear`. Template context key `row` (a dict: `spec`, `value`, `display_value`, `revealed`, `editing`, `has_override`) consumed by `_environment_row.html`; `groups` (`list[tuple[str, list[row-dict]]]`) consumed by `_environment_list.html`.

- [ ] **Step 1: Add the `env_vars` import and row-context helper to `server_admin/main.py`**

Add to the imports near the top (alongside the existing `from . import config`):

```python
from . import config, env_vars
```

Replace the existing line:

```python
from . import config
```

with the combined import above (there is exactly one `from . import config` line in the file, immediately before `from .formatting import ...`).

Add this helper function directly below `_service_rows()` (which ends just before the `@app.get("/services", ...)` route):

```python
def _environment_row_context(spec: env_vars.EnvVarSpec, *, revealed: bool = False, editing: bool = False) -> dict:
    value = env_vars.current_value(spec.key)
    display_value = value if (not spec.sensitive or revealed) else env_vars.mask(value)
    return {
        "spec": spec,
        "value": value,
        "display_value": display_value,
        "revealed": revealed,
        "editing": editing,
        "has_override": env_vars.has_override(spec.key),
    }
```

- [ ] **Step 2: Add the environment routes to `server_admin/main.py`**

Add these routes at the end of the file (after the existing `logs_download` route):

```python
@app.get("/environment", response_class=HTMLResponse)
async def environment_page(request: Request):
    return templates.TemplateResponse(request, "environment.html", {"active_nav": "environment"})


@app.get("/environment/list", response_class=HTMLResponse)
async def environment_list(request: Request):
    groups = [
        (category, [_environment_row_context(spec) for spec in specs]) for category, specs in env_vars.categories()
    ]
    return templates.TemplateResponse(request, "_environment_list.html", {"groups": groups})


@app.get("/environment/{key}/view", response_class=HTMLResponse)
async def environment_view(request: Request, key: str):
    try:
        spec = env_vars.get_spec(key)
    except KeyError:
        return render_error(request, f"Unknown environment variable: {key}", status_code=404)
    return templates.TemplateResponse(request, "_environment_row.html", {"row": _environment_row_context(spec)})


@app.get("/environment/{key}/reveal", response_class=HTMLResponse)
async def environment_reveal(request: Request, key: str):
    try:
        spec = env_vars.get_spec(key)
    except KeyError:
        return render_error(request, f"Unknown environment variable: {key}", status_code=404)
    return templates.TemplateResponse(
        request, "_environment_row.html", {"row": _environment_row_context(spec, revealed=True)}
    )


@app.get("/environment/{key}/edit", response_class=HTMLResponse)
async def environment_edit(request: Request, key: str):
    try:
        spec = env_vars.get_spec(key)
    except KeyError:
        return render_error(request, f"Unknown environment variable: {key}", status_code=404)
    return templates.TemplateResponse(
        request, "_environment_row.html", {"row": _environment_row_context(spec, editing=True)}
    )


@app.post("/environment/{key}", response_class=HTMLResponse)
async def environment_save(request: Request, key: str, value: str = Form("")):
    try:
        spec = env_vars.get_spec(key)
    except KeyError:
        return render_error(request, f"Unknown environment variable: {key}", status_code=404)
    try:
        await run_in_threadpool(env_vars.set_value, key, value)
    except OSError as exc:
        return render_error(
            request, f"Applied in memory, but failed to save to disk: {exc}", status_code=500
        )
    return templates.TemplateResponse(request, "_environment_row.html", {"row": _environment_row_context(spec)})


@app.post("/environment/{key}/clear", response_class=HTMLResponse)
async def environment_clear(request: Request, key: str):
    try:
        spec = env_vars.get_spec(key)
    except KeyError:
        return render_error(request, f"Unknown environment variable: {key}", status_code=404)
    try:
        await run_in_threadpool(env_vars.clear_value, key)
    except OSError as exc:
        return render_error(
            request, f"Applied in memory, but failed to save to disk: {exc}", status_code=500
        )
    return templates.TemplateResponse(request, "_environment_row.html", {"row": _environment_row_context(spec)})
```

- [ ] **Step 3: Create `server_admin/templates/environment.html`**

```html
{% extends "base.html" %}
{% block title %}Environment — Server Admin{% endblock %}
{% block content %}
<h1>Environment</h1>
<div id="environment-list" hx-get="/environment/list" hx-trigger="load" hx-swap="innerHTML"></div>
{% endblock %}
```

- [ ] **Step 4: Create `server_admin/templates/_environment_list.html`**

```html
{% for category, rows in groups %}
<h2 class="env-category">{{ category }}</h2>
<table class="service-table env-table">
  <thead>
    <tr>
      <th>Variable</th>
      <th>Description</th>
      <th>Value</th>
      <th>Apply</th>
      <th>Actions</th>
    </tr>
  </thead>
  <tbody>
    {% for row in rows %}
    {% include "_environment_row.html" %}
    {% endfor %}
  </tbody>
</table>
{% endfor %}
```

- [ ] **Step 5: Create `server_admin/templates/_environment_row.html`**

```html
<tr id="env-row-{{ row.spec.key }}">
  <td class="mono">{{ row.spec.key }}</td>
  <td>{{ row.spec.description }}</td>
  <td>
    {% if row.editing %}
    <form class="env-value-form" hx-post="/environment/{{ row.spec.key }}" hx-target="closest tr" hx-swap="outerHTML">
      <input class="env-input" type="text" name="value" value="{{ row.value or '' }}"
        {% if row.spec.default %}placeholder="default: {{ row.spec.default }}"{% endif %}>
      <div class="btn-row">
        <button class="btn btn--accent btn--sm" type="submit">Save</button>
        <button class="btn btn--sm" type="button"
          hx-get="/environment/{{ row.spec.key }}/view" hx-target="closest tr" hx-swap="outerHTML">Cancel</button>
      </div>
    </form>
    {% elif row.value %}
    <span class="mono">{{ row.display_value }}</span>
    {% if row.spec.sensitive %}
      {% if row.revealed %}
      <button class="btn btn--sm" hx-get="/environment/{{ row.spec.key }}/view" hx-target="closest tr" hx-swap="outerHTML">Hide</button>
      {% else %}
      <button class="btn btn--sm" hx-get="/environment/{{ row.spec.key }}/reveal" hx-target="closest tr" hx-swap="outerHTML">Reveal</button>
      {% endif %}
    {% endif %}
    {% else %}
    <span class="env-default">unset{% if row.spec.default %} (default: {{ row.spec.default }}){% endif %}</span>
    {% endif %}
  </td>
  <td>
    {% if row.spec.owner_service %}
    <button class="btn btn--sm env-restart-btn" data-service-name="{{ row.spec.owner_service }}"
      hx-post="/services/{{ row.spec.owner_service }}/restart" hx-swap="none">Restart to apply</button>
    {% else %}
    <span class="env-default">Applies after next pod restart</span>
    {% endif %}
  </td>
  <td>
    <div class="btn-row">
      {% if not row.editing %}
      <button class="btn btn--sm" hx-get="/environment/{{ row.spec.key }}/edit" hx-target="closest tr" hx-swap="outerHTML">Edit</button>
      {% endif %}
      {% if row.has_override %}
      <button class="btn btn--sm btn--danger" hx-post="/environment/{{ row.spec.key }}/clear" hx-target="closest tr" hx-swap="outerHTML">Clear override</button>
      {% endif %}
    </div>
  </td>
</tr>
```

- [ ] **Step 6: Add the nav entry to `server_admin/templates/base.html`**

Find this block (the last nav link, for Logs):

```html
        <a href="/logs" class="{{ 'is-active' if active_nav == 'logs' else '' }}">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>
          Logs
        </a>
      </nav>
```

Replace it with (adding the new link before `</nav>`):

```html
        <a href="/logs" class="{{ 'is-active' if active_nav == 'logs' else '' }}">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>
          Logs
        </a>
        <a href="/environment" class="{{ 'is-active' if active_nav == 'environment' else '' }}">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="21" x2="4" y2="14"></line><line x1="4" y1="10" x2="4" y2="3"></line><line x1="12" y1="21" x2="12" y2="12"></line><line x1="12" y1="8" x2="12" y2="3"></line><line x1="20" y1="21" x2="20" y2="16"></line><line x1="20" y1="12" x2="20" y2="3"></line><line x1="1" y1="14" x2="7" y2="14"></line><line x1="9" y1="8" x2="15" y2="8"></line><line x1="17" y1="16" x2="23" y2="16"></line></svg>
          Environment
        </a>
      </nav>
```

- [ ] **Step 7: Extend the restart-button toast listener in `server_admin/static/app.js`**

Find this function:

```javascript
  function initGpuProcessRestart() {
    document.body.addEventListener("htmx:afterRequest", function (evt) {
      var btn = evt.detail.elt;
      if (!btn.classList || !btn.classList.contains("gpu-process-restart-btn")) return;
      var name = btn.dataset.serviceName || "service";
      showToast(evt.detail.successful ? "Restarting " + name + "…" : "Failed to restart " + name);
    });
  }
```

Replace it with (renamed, and matching either restart-button class — both the GPU dashboard's existing button and the new Environment page's button post to `/services/{key}/restart` with `hx-swap="none"`, so there's no swapped content to show feedback in and both need the same toast):

```javascript
  function initServiceRestartButtons() {
    document.body.addEventListener("htmx:afterRequest", function (evt) {
      var btn = evt.detail.elt;
      if (!btn.classList) return;
      if (!btn.classList.contains("gpu-process-restart-btn") && !btn.classList.contains("env-restart-btn")) return;
      var name = btn.dataset.serviceName || "service";
      showToast(evt.detail.successful ? "Restarting " + name + "…" : "Failed to restart " + name);
    });
  }
```

Find this line in the `DOMContentLoaded` listener:

```javascript
    initGpuProcessRestart();
```

Replace it with:

```javascript
    initServiceRestartButtons();
```

- [ ] **Step 8: Add styling to `server_admin/static/style.css`**

Append at the end of the file:

```css
.env-category {
  margin: 2rem 0 0.75rem;
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.env-category:first-child { margin-top: 0; }

.env-table td { vertical-align: top; }

.env-input {
  width: 100%;
  max-width: 420px;
  background: var(--surface-2);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 0.45rem 0.7rem;
  font-family: var(--font-mono);
  font-size: 0.85rem;
}
.env-input:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }

.env-value-form { display: flex; flex-direction: column; gap: 0.5rem; align-items: flex-start; }

.env-default { color: var(--text-dim); font-style: italic; font-size: 0.85rem; }
```

- [ ] **Step 9: Manual verification — run the dev server and exercise the page**

```bash
cd /Users/thomasspitznas/Projects/runpod-stability-matrix
rm -rf /tmp/env-vars-dev
export SERVER_ADMIN_ENV_OVERRIDES_DIR=/tmp/env-vars-dev
python3 -m uvicorn server_admin.main:app --app-dir . --host 127.0.0.1 --port 8001 &
sleep 1
curl -s http://127.0.0.1:8001/environment | grep -o '<h1>Environment</h1>'
curl -s http://127.0.0.1:8001/environment/list | grep -c '<tr id="env-row-'
curl -s http://127.0.0.1:8001/environment/CIVITAI_MANAGER_LOG_LEVEL/edit | grep -o 'name="value"'
curl -s -X POST http://127.0.0.1:8001/environment/CIVITAI_MANAGER_LOG_LEVEL -d 'value=DEBUG' | grep -o 'DEBUG'
curl -s http://127.0.0.1:8001/environment/CIVITAI_API_TOKEN/reveal -o /dev/null -w '%{http_code}\n'
curl -s -X POST http://127.0.0.1:8001/environment/CIVITAI_MANAGER_LOG_LEVEL/clear | grep -o 'unset'
curl -s http://127.0.0.1:8001/environment/NOT_A_REAL_KEY/view -o /dev/null -w '%{http_code}\n'
kill %1
unset SERVER_ADMIN_ENV_OVERRIDES_DIR
rm -rf /tmp/env-vars-dev
```

Expected output, in order: `<h1>Environment</h1>`, a count of 30 (one `<tr id="env-row-...">` per registry entry), `name="value"`, `DEBUG`, `200`, `unset`, `404`.

Note: this dev run has no `SERVER_ADMIN_USERNAME`/`SERVER_ADMIN_PASSWORD` set, so `SessionAuthMiddleware` allows all requests through unauthenticated — matching how the other pages are manually tested in this repo. `SERVER_ADMIN_ENV_OVERRIDES_DIR` overrides `env_vars.py`'s default `/workspace/server-admin` path (same pattern as `supervisor.py`'s `SERVER_ADMIN_STATE_DIR`) — without it, this dev machine's read-only `/` means every `POST` would fail to persist (see Task 1 Step 5); in a real pod, `/workspace` exists and this variable is left unset.

- [ ] **Step 10: Commit**

```bash
git add server_admin/main.py server_admin/templates/environment.html server_admin/templates/_environment_list.html server_admin/templates/_environment_row.html server_admin/templates/base.html server_admin/static/app.js server_admin/static/style.css
git commit -m "$(cat <<'EOF'
feat: add Environment page to Server Admin

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Source the override file in `start.sh`

**Files:**
- Modify: `start.sh`

**Interfaces:**
- Consumes: the literal path `/workspace/server-admin/env-overrides.env`, which must match `env_vars.OVERRIDES_FILE` from Task 1.

- [ ] **Step 1: Add the sourcing block to the top of `start.sh`**

Find the current start of the file:

```bash
#!/bin/bash

mkdir -p /workspace/invokeai
mkdir -p /workspace/civitai-downloads
```

Replace it with:

```bash
#!/bin/bash

# Env var overrides saved via Server Admin's Environment page (server_admin/env_vars.py),
# persisted on the volume disk so they survive pod restarts. Sourced first,
# before anything else in this script, so every subsequent export and
# service launch sees them. `set -a` auto-exports every var assigned while
# sourcing, so the file itself doesn't need "export" prefixes.
ENV_OVERRIDES_FILE="/workspace/server-admin/env-overrides.env"
if [ -f "$ENV_OVERRIDES_FILE" ]; then
    set -a
    source "$ENV_OVERRIDES_FILE"
    set +a
fi

mkdir -p /workspace/invokeai
mkdir -p /workspace/civitai-downloads
```

- [ ] **Step 2: Manual verification — bash syntax check**

```bash
cd /Users/thomasspitznas/Projects/runpod-stability-matrix
bash -n start.sh
echo "exit code: $?"
```

Expected: `exit code: 0` (no syntax errors printed).

- [ ] **Step 3: Manual verification — dry-run the sourcing block in isolation**

```bash
mkdir -p /tmp/env-override-test/workspace/server-admin
cat > /tmp/env-override-test/workspace/server-admin/env-overrides.env <<'EOF'
CIVITAI_MANAGER_LOG_LEVEL=DEBUG
CIVITAI_BASE_URL='https://example.com/needs quoting'
EOF
bash -c '
ENV_OVERRIDES_FILE="/tmp/env-override-test/workspace/server-admin/env-overrides.env"
if [ -f "$ENV_OVERRIDES_FILE" ]; then
    set -a
    source "$ENV_OVERRIDES_FILE"
    set +a
fi
echo "LOG_LEVEL=$CIVITAI_MANAGER_LOG_LEVEL"
echo "BASE_URL=$CIVITAI_BASE_URL"
'
rm -rf /tmp/env-override-test
```

Expected:
```
LOG_LEVEL=DEBUG
BASE_URL=https://example.com/needs quoting
```

- [ ] **Step 4: Commit**

```bash
git add start.sh
git commit -m "$(cat <<'EOF'
feat: source Server Admin env var overrides on boot

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: nothing new — documents the behavior built in Tasks 1–3 (file path `/workspace/server-admin/env-overrides.env`, route prefix `/environment`, curated registry in `server_admin/env_vars.py`).

- [ ] **Step 1: Add a subsection to `CLAUDE.md`'s "Server Admin (port 8001)" section**

Find this paragraph (end of the Server Admin section, right before the `### aria2 download-to-folder` heading):

```
Log search (`server_admin/logs.py`'s `search_log()`) is a separate, on-demand code path from the always-polled tail (`tail_log()`) — it streams the file forward line-by-line (substring or opt-in regex, both ANSI-stripped before matching, since InvokeAI's real logs are ANSI-colored) rather than reusing the tail's backward-chunk read, which is tail-specific. `/logs/download/{service}` streams the raw log file for offline viewing/grepping.
```

Add this paragraph directly after it (still inside the Server Admin section, before `### aria2 download-to-folder`):

```
**Environment variable management** (`server_admin/env_vars.py`, `/environment` page): a curated `REGISTRY` of `EnvVarSpec` entries (app-relevant vars plus CUDA vars — not all of `os.environ`, to keep the exposed surface bounded) backs a view/edit UI. Edits are written to `/workspace/server-admin/env-overrides.env` (`KEY=value`, `shlex.quote`d) — a new file on the volume disk, distinct from the supervisor's ephemeral `/tmp/server-admin/` state dir — and `start.sh` sources it as the very first thing it does, so overrides win over the pod's originally injected values and survive a pod restart. Saving also live-applies the value to Server Admin's own `os.environ` immediately, so restarting the owning service (`EnvVarSpec.owner_service`, a `server_admin.supervisor.SERVICES` key) via the existing `POST /services/{key}/restart` picks up the change right away without a pod restart. Vars with `owner_service=None` — `SERVER_ADMIN_*` (Server Admin isn't itself a supervised service, so it can't restart itself) and `ARIA2_RPC_SECRET` (baked into `aria2-rpc`'s launch command at `supervisor.py` import time, so a supervised restart wouldn't actually pick up a new value — see that `ServiceSpec`'s `start_cmd`) — are pod-restart-only; the UI shows a static note instead of a restart button for these. Sensitive values are masked by default and only sent to the browser in full via an explicit reveal action. Only registry keys are editable — there's no way to add an arbitrary new env var name through this UI.
```

- [ ] **Step 2: Add an invariant bullet to `AGENTS.md`**

Find this bullet in the "Critical Invariants (Do Not Break)" section:

```
- Don't relax the `/downloads`-page install-time sidecar tracking — Downloads-page installs and direct-`/install`-button installs are separate code paths ([civitai_manager/main.py](civitai_manager/main.py)'s `_track_download_install` and `_track_install_metadata`) that both need to stay wired to [civitai_manager/metadata_store.py](civitai_manager/metadata_store.py).
```

Add this bullet directly after it:

```
- Keep [server_admin/env_vars.py](server_admin/env_vars.py)'s `REGISTRY` as the only editable env var surface — don't let `/environment` grow a path to add arbitrary new keys. If a var's `owner_service` is `None`, don't wire a restart button for it (it's pod-restart-only on purpose — either it's a `SERVER_ADMIN_*` var, or, like `ARIA2_RPC_SECRET`, its owning process only reads it at supervisor-import time).
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md AGENTS.md
git commit -m "$(cat <<'EOF'
docs: document Server Admin env var management

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: End-to-end manual verification

**Files:** none (verification only).

**Interfaces:** none — exercises the full stack from Tasks 1–4 together.

- [ ] **Step 1: Full local smoke test of the Environment page**

```bash
cd /Users/thomasspitznas/Projects/runpod-stability-matrix
rm -rf /tmp/env-e2e-overrides
export SERVER_ADMIN_ENV_OVERRIDES_DIR=/tmp/env-e2e-overrides
python3 -m uvicorn server_admin.main:app --app-dir . --host 127.0.0.1 --port 8001 &
SERVER_PID=$!
sleep 1

echo "--- list page renders all categories ---"
curl -s http://127.0.0.1:8001/environment/list | grep -o 'env-category">[^<]*' | sort -u

echo "--- edit -> save -> row shows new value ---"
curl -s http://127.0.0.1:8001/environment/CIVITAI_CACHE_TTL_SECONDS/edit | grep -o 'placeholder="default: 3600"'
curl -s -X POST http://127.0.0.1:8001/environment/CIVITAI_CACHE_TTL_SECONDS -d 'value=7200' | grep -o '7200'

echo "--- sensitive var masked by default, revealed on demand ---"
curl -s -X POST http://127.0.0.1:8001/environment/CIVITAI_API_TOKEN -d 'value=sk-verysecrettoken1234'
curl -s http://127.0.0.1:8001/environment/CIVITAI_API_TOKEN/view | grep -o '•*1234'
curl -s http://127.0.0.1:8001/environment/CIVITAI_API_TOKEN/reveal | grep -o 'sk-verysecrettoken1234'

echo "--- pod-restart-only var shows note, not a restart button ---"
curl -s http://127.0.0.1:8001/environment/SERVER_ADMIN_USERNAME/view | grep -o 'Applies after next pod restart'
curl -s http://127.0.0.1:8001/environment/ARIA2_RPC_SECRET/view | grep -o 'Applies after next pod restart'

echo "--- owner_service var shows a restart button targeting the right service ---"
curl -s http://127.0.0.1:8001/environment/CIVITAI_CACHE_TTL_SECONDS/view | grep -o 'hx-post="/services/civitai-manager/restart"'

echo "--- clear override reverts and hides the clear button ---"
curl -s -X POST http://127.0.0.1:8001/environment/CIVITAI_CACHE_TTL_SECONDS/clear | grep -o 'default: 3600'
curl -s http://127.0.0.1:8001/environment/CIVITAI_CACHE_TTL_SECONDS/view | grep -c 'Clear override'

echo "--- override file on disk reflects only the still-set var ---"
cat /tmp/env-e2e-overrides/env-overrides.env

kill $SERVER_PID
unset SERVER_ADMIN_ENV_OVERRIDES_DIR
rm -rf /tmp/env-e2e-overrides
```

Expected output, in order:
```
--- list page renders all categories ---
env-category">CivitAI Manager
env-category">InvokeAI / CUDA
env-category">OneDrive Sync Manager
env-category">Server Admin
env-category">aria2 / Downloads
--- edit -> save -> row shows new value ---
placeholder="default: 3600"
7200
--- sensitive var masked by default, revealed on demand ---
•1234
sk-verysecrettoken1234
--- pod-restart-only var shows note, not a restart button ---
Applies after next pod restart
Applies after next pod restart
--- owner_service var shows a restart button targeting the right service ---
hx-post="/services/civitai-manager/restart"
--- clear override reverts and hides the clear button ---
default: 3600
0
--- override file on disk reflects only the still-set var ---
CIVITAI_API_TOKEN=sk-verysecrettoken1234
```

(Category sort order from `sort -u` is alphabetical, not registry order — that's expected and fine, it's just for this grep check. `CIVITAI_CACHE_TTL_SECONDS` doesn't appear in the final file dump because it was cleared in the previous step.)

- [ ] **Step 2: Confirm Task 1's persistence-failure regression check still passes against the real default path**

Re-run Task 1 Step 5 verbatim (it hardcodes `/workspace/server-admin`, not the `SERVER_ADMIN_ENV_OVERRIDES_DIR` override) to confirm the fallback default is unaffected by this task's changes:

```bash
python3 -c "
import os
from pathlib import Path
from server_admin import env_vars

env_vars.OVERRIDES_DIR = Path('/workspace/server-admin')
env_vars.OVERRIDES_FILE = env_vars.OVERRIDES_DIR / 'env-overrides.env'
env_vars._ORIGINAL_ENV = dict(os.environ)

key = 'CIVITAI_MANAGER_LOG_LEVEL'
try:
    env_vars.set_value(key, 'DEBUG')
    raise AssertionError('expected an OSError from the unwritable /workspace path')
except OSError:
    pass
assert env_vars.current_value(key) == 'DEBUG'
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 3: No commit for this task** — it's verification only, not a code change.

---

## Post-plan

Once all tasks are complete, follow the `superpowers:finishing-a-development-branch` skill to decide how to land this branch (push + PR is consistent with how `feature/server-admin-enhancements` and `chore/agents-md-and-ui-tweaks` were handled earlier in this project).
