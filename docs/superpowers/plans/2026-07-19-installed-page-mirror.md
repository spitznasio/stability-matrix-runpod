# Installed Page Mirror Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/installed` functionally equivalent to `/browse` — clickable model cards that open a rich CivitAI-metadata detail page — by capturing CivitAI model data at install time and persisting it as sidecar JSON files.

**Architecture:** `POST /install` is extended to accept `model_id`/`version_id` and spawns a server-side background task (`asyncio.create_task`) that polls InvokeAI's install job independently of any client, then writes a metadata sidecar to `/workspace/civitai-metadata/` keyed by a hash of the installed model's on-disk path. `GET /installed` and a new `GET /installed/{path_hash}` route read these sidecars to enrich InvokeAI's bare model list with CivitAI data. The Installed page's grid/table move from client-side-only JS rendering to server-rendered Jinja partials (mirroring Browse), with the existing filter/sort JS adapted to operate on real DOM nodes instead of rebuilding them.

**Tech Stack:** FastAPI, Jinja2, htmx, vanilla JS (no build step), existing `httpx`-based `CivitAIClient`/`InvokeAIClient`.

## Global Constraints

- No test framework is installed in this project (`civitai_manager/` has zero automated tests). Verification steps in this plan use `python3 -c` smoke checks for pure functions and manual `curl`/browser checks for routes and templates — do not introduce `pytest` or any new test dependency.
- `/workspace/civitai-metadata/` is the sidecar storage location (persists across pod restarts), per the approved spec.
- Sidecar filenames are `sha256(model_path).json` — no separate index file.
- The exact field in InvokeAI's completed install-job payload that holds the installed model's on-disk path is **not confirmed** anywhere in this codebase or its docs. Task 3 includes a defensive multi-key extraction helper and an explicit live-verification step (per `CLAUDE.md`'s own documented troubleshooting pattern: curl the pod directly) — do not assume a single field name without that verification.
- Follow existing code conventions: `dict | None` style type hints, `httpx.HTTPError` catch-and-log-warning around upstream calls, `Path` from `pathlib`, no comments beyond one-liners explaining non-obvious "why".
- Sanitized HTML (`description` fields) must reuse the existing bleach pipeline already applied inside `CivitAIClient.get_model` — never re-sanitize or bypass it.

---

## File Structure

- **Modify:** `civitai_manager/config.py` — add `CIVITAI_METADATA_DIR`
- **Create:** `civitai_manager/metadata_store.py` — sidecar read/write/hash helpers for installed-model metadata (parallel to `downloads.py`'s sidecar helpers, but keyed by path hash instead of `<file>.civitai.json`)
- **Modify:** `civitai_manager/main.py` — extend `POST /install`, add background tracking task + pure sidecar-builder function, enrich `GET /installed`, add `GET /installed/{path_hash}`
- **Modify:** `civitai_manager/templates/_install_panel.html` — add `model_id`/`version_id` hidden fields to the Install form
- **Modify:** `civitai_manager/templates/_version_body.html` — add `model_id`/`version_id` hidden fields to the "other files" Install form
- **Create:** `civitai_manager/templates/_installed_card.html` — card partial for the Installed grid (badge, version, distinguishing style)
- **Create:** `civitai_manager/templates/installed_detail.html` — detail page mirroring `model_detail.html`
- **Modify:** `civitai_manager/templates/installed.html` — server-render grid/table via Jinja loops instead of client-only rendering
- **Modify:** `civitai_manager/static/app.js` — rewrite `initInstalledTable` to filter/sort real DOM nodes instead of rebuilding them from sentinel spans
- **Modify:** `civitai_manager/static/style.css` — remove now-unused old `.installed-card__*` rules, add badge/version/distinguishing-border styles

---

### Task 1: Metadata storage module

**Files:**
- Modify: `civitai_manager/config.py`
- Create: `civitai_manager/metadata_store.py`

**Interfaces:**
- Produces: `metadata_store.path_hash(model_path: str) -> str`, `metadata_store.write_sidecar(model_path: str, metadata: dict) -> None`, `metadata_store.read_sidecar(model_path: str) -> dict | None`

- [ ] **Step 1: Add `CIVITAI_METADATA_DIR` to config**

Add to `civitai_manager/config.py`, after the `CIVITAI_DOWNLOAD_DIR`/`ARIA2_*` block:

```python
# Sidecar metadata captured for models installed via the app's "Install"
# button — see metadata_store.py. Lives on the volume disk (like
# CIVITAI_DOWNLOAD_DIR) so it survives pod restarts.
CIVITAI_METADATA_DIR = os.environ.get("CIVITAI_METADATA_DIR", "/workspace/civitai-metadata")
```

- [ ] **Step 2: Write `metadata_store.py`**

Create `civitai_manager/metadata_store.py`:

```python
import hashlib
import json
import logging
from pathlib import Path

from . import config

logger = logging.getLogger(__name__)


def path_hash(model_path: str) -> str:
    return hashlib.sha256(model_path.encode("utf-8")).hexdigest()


def _sidecar_path(model_path: str) -> Path:
    return Path(config.CIVITAI_METADATA_DIR) / f"{path_hash(model_path)}.json"


def write_sidecar(model_path: str, metadata: dict) -> None:
    target = _sidecar_path(model_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(metadata, indent=2))


def read_sidecar(model_path: str) -> dict | None:
    target = _sidecar_path(model_path)
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text())
    except (OSError, json.JSONDecodeError):
        logger.warning("Failed to read metadata sidecar %s", target, exc_info=True)
        return None
```

- [ ] **Step 3: Smoke-test the round trip**

Run:

```bash
cd /Users/thomasspitznas/Projects/runpod-stability-matrix && python3 -c "
import tempfile, os
os.environ['CIVITAI_METADATA_DIR'] = tempfile.mkdtemp()
from civitai_manager import metadata_store

p = '/workspace/invokeai/models/sd-1/main/foo.safetensors'
assert metadata_store.read_sidecar(p) is None, 'expected None before write'

metadata_store.write_sidecar(p, {'civitai_model_id': 123, 'model_name': 'Foo'})
result = metadata_store.read_sidecar(p)
assert result == {'civitai_model_id': 123, 'model_name': 'Foo'}, result

h1 = metadata_store.path_hash(p)
h2 = metadata_store.path_hash(p)
assert h1 == h2 and len(h1) == 64, 'hash must be stable sha256 hex'
assert metadata_store.path_hash('other/path') != h1, 'different paths must hash differently'

print('OK')
"
```

Expected: `OK` printed, no assertion errors.

- [ ] **Step 4: Smoke-test malformed JSON fallback**

Run:

```bash
cd /Users/thomasspitznas/Projects/runpod-stability-matrix && python3 -c "
import tempfile, os
tmp = tempfile.mkdtemp()
os.environ['CIVITAI_METADATA_DIR'] = tmp
from civitai_manager import metadata_store

p = '/workspace/invokeai/models/sd-1/main/bar.safetensors'
sidecar = metadata_store._sidecar_path(p)
sidecar.parent.mkdir(parents=True, exist_ok=True)
sidecar.write_text('{not valid json')

result = metadata_store.read_sidecar(p)
assert result is None, f'expected None for malformed JSON, got {result!r}'
print('OK')
"
```

Expected: `OK` printed (malformed JSON degrades to `None`, doesn't raise).

- [ ] **Step 5: Commit**

```bash
git add civitai_manager/config.py civitai_manager/metadata_store.py
git commit -m "$(cat <<'EOF'
feat: add metadata_store module for installed-model CivitAI sidecars

Sidecars are keyed by sha256(model_path) and stored under
CIVITAI_METADATA_DIR so they persist across pod restarts, same
pattern as the Downloads feature's file sidecars.
EOF
)"
```

---

### Task 2: Capture CivitAI metadata on install (background task, decoupled from client polling)

**Files:**
- Modify: `civitai_manager/main.py`
- Modify: `civitai_manager/templates/_install_panel.html`
- Modify: `civitai_manager/templates/_version_body.html`

**Interfaces:**
- Consumes: `metadata_store.write_sidecar`, `metadata_store.path_hash` (Task 1); `CivitAIClient.get_model(model_id: int, refresh: bool = False) -> dict` (existing); `InvokeAIClient.get_install_job(job_id: str) -> dict` (existing)
- Produces: `main._build_sidecar_metadata(model: dict, version_id: int) -> dict` (pure function, used again in Task 4's testing); `main._extract_installed_path(job: dict) -> str | None`; `main._track_install_metadata(app, job_id, model_id, version_id) -> None` (background coroutine)

- [ ] **Step 1: Add hidden `model_id`/`version_id` fields to both Install forms**

In `civitai_manager/templates/_install_panel.html`, the primary Install form currently reads:

```html
      <form hx-post="/install" hx-target="#status-messages-{{ active_version.id }}" hx-swap="innerHTML">
        <input type="hidden" name="download_url" value="{{ f.downloadUrl }}">
        <button type="submit" class="btn btn--accent">Install</button>
      </form>
```

Change to:

```html
      <form hx-post="/install" hx-target="#status-messages-{{ active_version.id }}" hx-swap="innerHTML">
        <input type="hidden" name="download_url" value="{{ f.downloadUrl }}">
        <input type="hidden" name="model_id" value="{{ model.id }}">
        <input type="hidden" name="version_id" value="{{ active_version.id }}">
        <button type="submit" class="btn btn--accent">Install</button>
      </form>
```

In `civitai_manager/templates/_version_body.html`, the "other files" Install form currently reads:

```html
        <form hx-post="/install" hx-target="#status-messages-{{ active_version.id }}" hx-swap="innerHTML">
          <input type="hidden" name="download_url" value="{{ file.downloadUrl }}">
          <button type="submit" class="btn btn--small">Install</button>
        </form>
```

Change to:

```html
        <form hx-post="/install" hx-target="#status-messages-{{ active_version.id }}" hx-swap="innerHTML">
          <input type="hidden" name="download_url" value="{{ file.downloadUrl }}">
          <input type="hidden" name="model_id" value="{{ model.id }}">
          <input type="hidden" name="version_id" value="{{ active_version.id }}">
          <button type="submit" class="btn btn--small">Install</button>
        </form>
```

- [ ] **Step 2: Add imports to `main.py`**

At the top of `civitai_manager/main.py`, add `asyncio` to the stdlib imports and `metadata_store` to the local imports:

```python
import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlencode

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from . import config, downloads, metadata_store
from .aria2_client import Aria2Client
from .aria2_client import TERMINAL_STATUSES as ARIA2_TERMINAL_STATUSES
from .civitai_client import CivitAIClient
from .formatting import format_commercial_use
from .invokeai_client import InvokeAIClient
from .sanitize import html_to_text
```

- [ ] **Step 3: Add the pure sidecar-builder function**

Add near the top of `main.py`, after the `BASE_MODEL_CHOICES` list:

```python
INSTALL_METADATA_POLL_SECONDS = 2.0


def _build_sidecar_metadata(model: dict, version_id: int) -> dict:
    version = next((v for v in model.get("modelVersions", []) if v.get("id") == version_id), None)
    creator = model.get("creator") or {}
    return {
        "civitai_model_id": model.get("id"),
        "civitai_version_id": version_id,
        "civitai_url": f"https://civitai.com/models/{model.get('id')}",
        "model_name": model.get("name"),
        "type": model.get("type"),
        "base_model": version.get("baseModel") if version else None,
        "creator_username": creator.get("username"),
        "description": model.get("description"),
        "trigger_words": (version.get("trainedWords") if version else None) or [],
        "tags": [t.get("name") if isinstance(t, dict) else t for t in (model.get("tags") or [])],
        "stats": model.get("stats"),
        "allowCommercialUse": model.get("allowCommercialUse"),
        "allowDerivatives": model.get("allowDerivatives"),
        "nsfw": model.get("nsfw"),
        "publishedAt": model.get("publishedAt"),
        "versions": [
            {
                "id": v.get("id"),
                "name": v.get("name"),
                "images": (v.get("images") or [])[:1],
            }
            for v in model.get("modelVersions", [])
        ],
        "installed_version_id": version_id,
        "installed_version_name": version.get("name") if version else None,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


def _extract_installed_path(job: dict) -> str | None:
    # The exact field InvokeAI uses for the installed model's on-disk path in a
    # completed ModelInstallJob payload has not been confirmed against a live
    # server as of writing — try the plausible locations. If none match on a
    # real pod, curl the job status endpoint directly (see CLAUDE.md's
    # troubleshooting note on /install) and add the real key here.
    config_out = job.get("config") if isinstance(job.get("config"), dict) else None
    if config_out and config_out.get("path"):
        return config_out["path"]
    config_out2 = job.get("config_out") if isinstance(job.get("config_out"), dict) else None
    if config_out2 and config_out2.get("path"):
        return config_out2["path"]
    if job.get("path"):
        return job["path"]
    return None
```

- [ ] **Step 4: Add the background tracking coroutine**

Add directly after `_extract_installed_path`:

```python
async def _track_install_metadata(app: FastAPI, job_id: str, model_id: int, version_id: int) -> None:
    # Runs independently of any client connection so metadata capture doesn't
    # depend on a browser tab staying open/visible for the entire install —
    # see docs/superpowers/specs/2026-07-19-installed-page-mirror-design.md,
    # "Server-side install-completion tracking".
    invokeai: InvokeAIClient = app.state.invokeai
    civitai: CivitAIClient = app.state.civitai
    while True:
        await asyncio.sleep(INSTALL_METADATA_POLL_SECONDS)
        try:
            job = await invokeai.get_install_job(job_id)
        except httpx.HTTPError:
            logger.warning(
                "Lost contact with InvokeAI tracking install job %s for metadata capture",
                job_id, exc_info=True,
            )
            return
        if job.get("status") not in TERMINAL_STATUSES:
            continue
        if job.get("status") != "completed":
            logger.info(
                "Install job %s did not complete successfully (status=%s); skipping metadata capture",
                job_id, job.get("status"),
            )
            return
        installed_path = _extract_installed_path(job)
        if not installed_path:
            logger.warning(
                "Install job %s completed but no installed path found in job payload; "
                "skipping metadata capture. job=%s", job_id, job,
            )
            return
        try:
            model = await civitai.get_model(model_id)
        except httpx.HTTPError:
            logger.warning(
                "Failed to fetch CivitAI model %s for metadata capture after install",
                model_id, exc_info=True,
            )
            return
        metadata_store.write_sidecar(installed_path, _build_sidecar_metadata(model, version_id))
        logger.info(
            "Captured install metadata for model_id=%s version_id=%s at %s",
            model_id, version_id, installed_path,
        )
        return
```

- [ ] **Step 5: Wire it into `POST /install`**

Replace the existing `install` handler:

```python
@app.post("/install", response_class=HTMLResponse)
async def install(request: Request, download_url: str = Form(...)):
    logger.info("Install requested: %s", download_url)
    try:
        job = await request.app.state.invokeai.install_model(
            download_url, config.CIVITAI_API_TOKEN
        )
    except httpx.HTTPError:
        logger.warning("Install request rejected by InvokeAI for %s", download_url, exc_info=True)
        return render_error(
            request,
            "InvokeAI is not ready yet, or the install request was rejected — try again shortly.",
        )
    logger.info("Install job %s started for %s (status=%s)", job.get("id"), download_url, job.get("status"))
    return templates.TemplateResponse(
        request,
        "_install_status.html",
        {"job": job, "terminal": job.get("status") in TERMINAL_STATUSES},
    )
```

with:

```python
@app.post("/install", response_class=HTMLResponse)
async def install(
    request: Request,
    download_url: str = Form(...),
    model_id: str = Form(""),
    version_id: str = Form(""),
):
    logger.info("Install requested: %s", download_url)
    try:
        job = await request.app.state.invokeai.install_model(
            download_url, config.CIVITAI_API_TOKEN
        )
    except httpx.HTTPError:
        logger.warning("Install request rejected by InvokeAI for %s", download_url, exc_info=True)
        return render_error(
            request,
            "InvokeAI is not ready yet, or the install request was rejected — try again shortly.",
        )
    logger.info("Install job %s started for %s (status=%s)", job.get("id"), download_url, job.get("status"))
    if job.get("id") and model_id and version_id:
        asyncio.create_task(
            _track_install_metadata(request.app, job["id"], int(model_id), int(version_id))
        )
    return templates.TemplateResponse(
        request,
        "_install_status.html",
        {"job": job, "terminal": job.get("status") in TERMINAL_STATUSES},
    )
```

- [ ] **Step 6: Smoke-test `_build_sidecar_metadata` in isolation**

Run:

```bash
cd /Users/thomasspitznas/Projects/runpod-stability-matrix && python3 -c "
from civitai_manager.main import _build_sidecar_metadata, _extract_installed_path

model = {
    'id': 12345,
    'name': 'Test Model',
    'type': 'Checkpoint',
    'creator': {'username': 'someone'},
    'description': '<p>desc</p>',
    'tags': [{'name': 'anime'}, 'realistic'],
    'stats': {'downloadCount': 100},
    'allowCommercialUse': 'Sell',
    'allowDerivatives': True,
    'nsfw': False,
    'publishedAt': '2025-01-01T00:00:00Z',
    'modelVersions': [
        {'id': 999, 'name': 'v1.0', 'baseModel': 'SDXL 1.0', 'trainedWords': ['trig1'], 'images': [{'url': 'https://x/1.png'}, {'url': 'https://x/2.png'}]},
        {'id': 998, 'name': 'v0.9', 'baseModel': 'SDXL 1.0', 'images': []},
    ],
}

sidecar = _build_sidecar_metadata(model, 999)
assert sidecar['civitai_model_id'] == 12345
assert sidecar['civitai_url'] == 'https://civitai.com/models/12345'
assert sidecar['base_model'] == 'SDXL 1.0'
assert sidecar['creator_username'] == 'someone'
assert sidecar['trigger_words'] == ['trig1']
assert sidecar['tags'] == ['anime', 'realistic']
assert sidecar['installed_version_id'] == 999
assert sidecar['installed_version_name'] == 'v1.0'
assert len(sidecar['versions']) == 2
assert len(sidecar['versions'][0]['images']) == 1, 'images should be truncated to first only'
assert 'captured_at' in sidecar

sidecar_unknown_version = _build_sidecar_metadata(model, 777)
assert sidecar_unknown_version['base_model'] is None
assert sidecar_unknown_version['installed_version_name'] is None

assert _extract_installed_path({'config': {'path': '/a/b'}}) == '/a/b'
assert _extract_installed_path({'config_out': {'path': '/c/d'}}) == '/c/d'
assert _extract_installed_path({'path': '/e/f'}) == '/e/f'
assert _extract_installed_path({}) is None

print('OK')
"
```

Expected: `OK` printed, no assertion errors.

- [ ] **Step 7: Manually verify against a live pod**

This step requires a running pod with InvokeAI and CivitAI Manager (per `CLAUDE.md`'s existing troubleshooting convention of curling the pod directly — there is no local mock for InvokeAI).

1. Install a model from `/browse`'s detail page via the "Install" button.
2. Tail the `civitai-manager` log (Server Admin's Logs page, or `/tmp/server-admin/logs/civitai-manager.log`) and confirm you see `Install job ... started` followed later by either `Captured install metadata for model_id=...` or a warning explaining why not.
3. If you see `no installed path found in job payload`, curl the job status directly to see the real shape:
   ```bash
   curl -s http://localhost:9090/api/v2/models/install/<job_id> | python3 -m json.tool
   ```
   Update `_extract_installed_path` in `main.py` with the correct key (redo Step 6's smoke test after any change) and re-verify.
4. Once captured, confirm the sidecar file exists:
   ```bash
   ls /workspace/civitai-metadata/
   cat /workspace/civitai-metadata/<hash>.json
   ```

- [ ] **Step 8: Commit**

```bash
git add civitai_manager/main.py civitai_manager/templates/_install_panel.html civitai_manager/templates/_version_body.html
git commit -m "$(cat <<'EOF'
feat: capture CivitAI metadata sidecar when installing a model

POST /install now spawns a server-side background task that polls
the InvokeAI install job independently of the client, so metadata
capture doesn't depend on a browser tab staying open for the whole
install (large checkpoints can take minutes).
EOF
)"
```

---

### Task 3: Enrich `GET /installed` and add `GET /installed/{path_hash}`

**Files:**
- Modify: `civitai_manager/main.py`

**Interfaces:**
- Consumes: `metadata_store.read_sidecar`, `metadata_store.path_hash` (Task 1)
- Produces: `GET /installed` now passes each model dict with `metadata` and `path_hash` keys added; `GET /installed/{path_hash}` route returning `installed_detail.html` (created in Task 5) with context keys `model`, `metadata`, `civitai_url`, `commercial_use_display`, `active_nav`

- [ ] **Step 1: Enrich the `/installed` list route**

Replace:

```python
@app.get("/installed", response_class=HTMLResponse)
async def installed(request: Request):
    try:
        models = await request.app.state.invokeai.list_models()
    except httpx.HTTPError:
        return templates.TemplateResponse(
            request,
            "installed.html",
            {"models": [], "error": "InvokeAI is not reachable right now.", "active_nav": "installed"},
        )
    return templates.TemplateResponse(
        request,
        "installed.html",
        {"models": models, "error": None, "active_nav": "installed"},
    )
```

with:

```python
@app.get("/installed", response_class=HTMLResponse)
async def installed(request: Request):
    try:
        models = await request.app.state.invokeai.list_models()
    except httpx.HTTPError:
        return templates.TemplateResponse(
            request,
            "installed.html",
            {"models": [], "error": "InvokeAI is not reachable right now.", "active_nav": "installed"},
        )
    for m in models:
        path = m.get("path")
        m["metadata"] = metadata_store.read_sidecar(path) if path else None
        m["path_hash"] = metadata_store.path_hash(path) if path else None
    return templates.TemplateResponse(
        request,
        "installed.html",
        {"models": models, "error": None, "active_nav": "installed"},
    )


@app.get("/installed/{path_hash}", response_class=HTMLResponse)
async def installed_detail(request: Request, path_hash: str):
    try:
        models = await request.app.state.invokeai.list_models()
    except httpx.HTTPError:
        return render_error(request, "InvokeAI is not reachable right now.", status_code=502)
    model = next(
        (m for m in models if m.get("path") and metadata_store.path_hash(m["path"]) == path_hash),
        None,
    )
    if model is None:
        return render_error(request, "That installed model could not be found.", status_code=404)
    metadata = metadata_store.read_sidecar(model["path"])
    context = {
        "request": request,
        "model": model,
        "metadata": metadata,
        "active_nav": "installed",
        "civitai_url": metadata.get("civitai_url") if metadata else None,
        "commercial_use_display": (
            format_commercial_use(metadata.get("allowCommercialUse")) if metadata else None
        ),
    }
    return templates.TemplateResponse(request, "installed_detail.html", context)
```

(`installed_detail.html` doesn't exist yet — created in Task 5. This route will 500 on the missing template until then; that's expected and resolved by the end of Task 5.)

- [ ] **Step 2: Verify route logic with a curl smoke test (grid enrichment only, doesn't need the detail template yet)**

Run against a live pod (or skip to Task 5 and verify both routes together — see that task's verification step):

```bash
curl -s http://localhost:8000/installed | grep -o 'No models installed\|installed-root' | head -1
```

Expected: some output confirming the page still renders (not a 500) — full visual verification happens once `installed.html` is updated in Task 6.

- [ ] **Step 3: Commit**

```bash
git add civitai_manager/main.py
git commit -m "$(cat <<'EOF'
feat: enrich /installed with metadata sidecars, add /installed/{hash}

Note: /installed/{path_hash} references installed_detail.html, added
in the next commit — this commit alone will 500 on that route.
EOF
)"
```

---

### Task 4: `_installed_card.html` partial and its CSS

**Files:**
- Create: `civitai_manager/templates/_installed_card.html`
- Modify: `civitai_manager/static/style.css`

**Interfaces:**
- Consumes: a `model` dict in Jinja scope with keys `name`, `type`, `base`, `path`, `path_hash`, and optional `metadata` dict (from Task 3) with keys `creator_username`, `installed_version_name`, `versions` (list of `{id, name, images: [{url}]}`)

- [ ] **Step 1: Remove now-unused old `.installed-card__*` CSS rules**

These belonged to the old JS-built card (removed in Task 6) and would otherwise silently conflict with the new card's class names. In `civitai_manager/static/style.css`, delete this block:

```css
.installed-card { display: block; background: var(--surface); border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
.installed-card:hover { border-color: var(--border-strong); }
.installed-card__swatch { aspect-ratio: 2.4/1; }
.installed-card__body { padding: 0.7rem 0.85rem 0.85rem; }
.installed-card__name { font-weight: 600; font-size: 0.9rem; margin-bottom: 0.4rem; word-break: break-word; }
.installed-card__row { display: flex; align-items: center; flex-wrap: wrap; gap: 0.35rem 0.5rem; margin-bottom: 0.5rem; }
.installed-card__base { font-family: var(--font-mono); font-size: 0.75rem; color: var(--text-dim); margin-left: auto; }
.installed-card__path { font-family: var(--font-mono); font-size: 0.72rem; color: var(--text-dim); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
```

- [ ] **Step 2: Add new styles for the badge, version label, and distinguishing card border**

Add in its place:

```css
.card.installed-card { border-color: var(--accent-soft); }
.card.installed-card:hover, .card.installed-card:focus-visible { border-color: var(--accent-text); }
.card__thumb { position: relative; }
.installed-card__badge { position: absolute; top: 0.5rem; right: 0.5rem; background: rgba(0, 0, 0, 0.55); }
.installed-card__version { font-family: var(--font-mono); font-size: 0.72rem; color: var(--accent-text); }
```

(`.installed-card__badge` reuses the existing `.stamp` base class for its border/text-color/padding — this rule only overrides position and background so it reads clearly over a thumbnail image.)

- [ ] **Step 3: Write the card partial**

Create `civitai_manager/templates/_installed_card.html`:

```html
<a class="card installed-card" href="/installed/{{ model.path_hash }}"
   data-installed-row
   data-name="{{ model.get('name', '—') }}"
   data-type="{{ model.get('type', '—') }}"
   data-base="{{ model.get('base', '—') }}"
   data-path="{{ model.get('path', '—') }}">
  {% set thumb = model.metadata.versions[0].images[0].url if model.metadata and model.metadata.versions and model.metadata.versions[0].images else None %}
  <div class="card__thumb">
    {% if thumb %}
      <img src="{{ thumb }}" alt="{{ model.get('name', '') }}" loading="lazy">
    {% else %}
      <div class="card__thumb--empty">NO PREVIEW</div>
    {% endif %}
    <span class="stamp installed-card__badge">INSTALLED</span>
  </div>
  <div class="card__body">
    <div class="card__name">{{ model.get('name', '—') }}</div>
    <div class="card__row">
      <span class="card__type">{{ model.get('type', '—') }}</span>
      {% if model.metadata and model.metadata.installed_version_name %}
      <span class="installed-card__version">{{ model.metadata.installed_version_name }} installed</span>
      {% endif %}
    </div>
    <div class="card__creator">{{ model.metadata.creator_username if model.metadata else "local model" }}</div>
  </div>
</a>
```

- [ ] **Step 4: Verify it's well-formed Jinja by rendering it standalone**

Run:

```bash
cd /Users/thomasspitznas/Projects/runpod-stability-matrix && python3 -c "
from jinja2 import Environment, FileSystemLoader

env = Environment(loader=FileSystemLoader('civitai_manager/templates'))
tmpl = env.get_template('_installed_card.html')

# With metadata
out = tmpl.render(model={
    'name': 'Test Model', 'type': 'Checkpoint', 'base': 'SDXL 1.0',
    'path': '/workspace/invokeai/models/x.safetensors', 'path_hash': 'abc123',
    'metadata': {
        'creator_username': 'someone', 'installed_version_name': 'v1.0',
        'versions': [{'id': 1, 'name': 'v1.0', 'images': [{'url': 'https://x/1.png'}]}],
    },
})
assert 'Test Model' in out and 'v1.0 installed' in out and 'https://x/1.png' in out and 'INSTALLED' in out

# Without metadata (local-only)
out2 = tmpl.render(model={
    'name': 'Bare Model', 'type': 'LORA', 'base': 'SD 1.5',
    'path': '/x/y.safetensors', 'path_hash': 'def456', 'metadata': None,
})
assert 'Bare Model' in out2 and 'NO PREVIEW' in out2 and 'local model' in out2
assert 'installed installed' not in out2

print('OK')
"
```

Expected: `OK` printed.

- [ ] **Step 5: Commit**

```bash
git add civitai_manager/templates/_installed_card.html civitai_manager/static/style.css
git commit -m "$(cat <<'EOF'
feat: add installed-model card partial with badge and version label
EOF
)"
```

---

### Task 5: `installed_detail.html` detail page

**Files:**
- Create: `civitai_manager/templates/installed_detail.html`

**Interfaces:**
- Consumes: context from `GET /installed/{path_hash}` (Task 3): `model` (dict with `name`/`type`/`base`/`path`), `metadata` (dict or `None`, shape from `_build_sidecar_metadata` in Task 2), `civitai_url` (str or `None`), `commercial_use_display` (str or `None`)

- [ ] **Step 1: Write the template**

Create `civitai_manager/templates/installed_detail.html`:

```html
{% extends "base.html" %}
{% block title %}{{ (metadata.model_name if metadata else model.get('name')) or "Installed Model" }} — CivitAI Manager{% endblock %}
{% block content %}
<div class="detail-shell">
  <a class="back-link" href="/installed">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"></polyline></svg>
    Back to installed
  </a>

  <div class="detail-head">
    <div class="detail-head__row">
      <h1 class="detail-head__title">{{ (metadata.model_name if metadata else model.get('name')) or "—" }}</h1>
      {% if civitai_url %}
      <a class="civitai-link" href="{{ civitai_url }}" target="_blank" rel="noopener">
        View on CivitAI
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg>
      </a>
      {% endif %}
    </div>
    <div class="tag-row">
      <span class="tag tag--type">{{ model.get('type', '—') }}</span>
      <span class="stamp">INSTALLED</span>
      {% if metadata and metadata.tags %}
        {% for t in metadata.tags %}<span class="tag">{{ t }}</span>{% endfor %}
      {% endif %}
    </div>
  </div>

  <div class="detail-grid">
    <div class="main-pane">
      {% if metadata and metadata.description %}
      {# sanitized in CivitAIClient.get_model (bleach allowlist) before this was captured to the sidecar #}
      <div class="desc is-collapsed" id="model-desc">{{ metadata.description | safe }}</div>
      <button type="button" class="desc-toggle" onclick="var el=document.getElementById('model-desc'); var collapsed=el.classList.toggle('is-collapsed'); this.textContent = collapsed ? 'Show more' : 'Show less';">Show more</button>
      {% endif %}

      {% if metadata and metadata.trigger_words %}
      <p class="trigger-words">
        {% for w in metadata.trigger_words %}<code>{{ w }}</code>{% endfor %}
      </p>
      {% endif %}

      {% if metadata and metadata.versions %}
      <div class="version-tabs">
        {% for v in metadata.versions %}
        <span class="version-tab{{ ' is-active' if v.id == metadata.installed_version_id else '' }}">{{ v.name }}{{ ' (installed)' if v.id == metadata.installed_version_id else '' }}</span>
        {% endfor %}
      </div>
      {% endif %}

      {% if not metadata %}
      <p class="empty">No CivitAI metadata available for this model — it may have been installed outside CivitAI Manager, moved on disk since install, or installed before this feature was added.</p>
      {% endif %}
    </div>

    <aside class="sidebar">
      <div class="panel">
        <div class="panel__heading">Install info</div>
        <div class="stat-list">
          <div class="stat-row"><span class="stat-row__label">Path</span><span class="stat-row__value installed-table__path">{{ model.get('path', '—') }}</span></div>
          <div class="stat-row"><span class="stat-row__label">Type</span><span class="stat-row__value">{{ model.get('type', '—') }}</span></div>
          <div class="stat-row"><span class="stat-row__label">Base model</span><span class="stat-row__value">{{ model.get('base', '—') }}</span></div>
        </div>
      </div>
      {% if metadata %}
      <div class="panel">
        <div class="panel__heading">CivitAI details</div>
        <div class="stat-list">
          <div class="stat-row"><span class="stat-row__label">By</span><span class="stat-row__value">{{ metadata.creator_username or "unknown" }}</span></div>
          <div class="stat-row"><span class="stat-row__label">Rating</span><span class="stat-row__value">{{ "%.1f"|format(metadata.stats.rating) if metadata.stats and metadata.stats.rating else "—" }}{% if metadata.stats and metadata.stats.ratingCount %} ({{ "{:,}".format(metadata.stats.ratingCount) }}){% endif %}</span></div>
          <div class="stat-row"><span class="stat-row__label">Downloads</span><span class="stat-row__value">{{ "{:,}".format(metadata.stats.downloadCount) if metadata.stats and metadata.stats.downloadCount else 0 }}</span></div>
          <div class="stat-row"><span class="stat-row__label">Published</span><span class="stat-row__value">{{ metadata.publishedAt[:10] if metadata.publishedAt else "—" }}</span></div>
          <div class="stat-row"><span class="stat-row__label">License</span><span class="stat-row__value">{{ commercial_use_display or "—" }}</span></div>
          <div class="stat-row"><span class="stat-row__label">Derivatives</span><span class="stat-row__value">{{ "Allowed" if metadata.allowDerivatives else "Not allowed" }}</span></div>
          <div class="stat-row"><span class="stat-row__label">NSFW</span><span class="stat-row__value">{{ "Yes" if metadata.nsfw else "No" }}</span></div>
        </div>
      </div>
      {% endif %}
    </aside>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 2: Verify it renders standalone for both the metadata and no-metadata cases**

Run:

```bash
cd /Users/thomasspitznas/Projects/runpod-stability-matrix && python3 -c "
from jinja2 import Environment, FileSystemLoader

env = Environment(loader=FileSystemLoader('civitai_manager/templates'))
tmpl = env.get_template('installed_detail.html')

metadata = {
    'model_name': 'Full Model', 'tags': ['anime'],
    'description': '<p>hello</p>', 'trigger_words': ['trig1'],
    'versions': [{'id': 1, 'name': 'v1.0'}, {'id': 2, 'name': 'v0.9'}],
    'installed_version_id': 1,
    'creator_username': 'someone',
    'stats': {'rating': 4.5, 'ratingCount': 10, 'downloadCount': 500},
    'publishedAt': '2025-01-01T00:00:00Z',
    'allowDerivatives': True, 'nsfw': False,
}
model = {'name': 'Full Model', 'type': 'Checkpoint', 'base': 'SDXL 1.0', 'path': '/x/y.safetensors'}

out = tmpl.render(model=model, metadata=metadata, civitai_url='https://civitai.com/models/1', commercial_use_display='Sell')
assert 'Full Model' in out
assert 'View on CivitAI' in out
assert 'trig1' in out
assert 'v1.0 (installed)' in out
assert 'someone' in out
assert '500' in out
assert 'Sell' in out
assert 'No CivitAI metadata available' not in out

out_bare = tmpl.render(model=model, metadata=None, civitai_url=None, commercial_use_display=None)
assert 'No CivitAI metadata available' in out_bare
assert 'View on CivitAI' not in out_bare

print('OK')
"
```

Expected: `OK` printed.

- [ ] **Step 3: Manually verify the live route now works end-to-end**

Against a live pod, with a model that has a captured sidecar (from Task 2's Step 7):

```bash
curl -s http://localhost:8000/installed/<the-hash-from-task-2> | grep -o 'View on CivitAI\|No CivitAI metadata' | head -1
```

Expected: `View on CivitAI` (if the model has a sidecar) — confirming the route Task 3 added now resolves correctly against this template. Also spot-check a model without a sidecar 404s or renders local-only cleanly, and that `/installed/does-not-exist` returns a 404 via `render_error`.

- [ ] **Step 4: Commit**

```bash
git add civitai_manager/templates/installed_detail.html
git commit -m "$(cat <<'EOF'
feat: add installed model detail page mirroring Browse's detail view
EOF
)"
```

---

### Task 6: Server-render the Installed grid/table, adapt filter/sort JS

**Files:**
- Modify: `civitai_manager/templates/installed.html`
- Modify: `civitai_manager/static/app.js`

**Interfaces:**
- Consumes: `_installed_card.html` (Task 4), enriched `models` list from `GET /installed` (Task 3, each with `metadata`/`path_hash` keys)

- [ ] **Step 1: Rewrite `installed.html`**

Replace the full contents of `civitai_manager/templates/installed.html`:

```html
{% extends "base.html" %}
{% block title %}Installed — CivitAI Manager{% endblock %}
{% block content %}
<div class="shell" style="padding-top: 1.5rem;">
<h1 class="page__title">Installed Models</h1>
{% if error %}
  <p class="empty">{{ error }}</p>
{% elif models %}
<div id="installed-root">
  <div class="installed-toolbar">
    <div class="search-wrap installed-search">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
      <input class="field field--query" id="installed-filter" placeholder="filter installed models…">
    </div>
    <select class="field" id="installed-type-filter">
      <option value="">All types</option>
      {% for t in models | map(attribute='type') | unique | sort %}
      <option value="{{ t }}">{{ t }}</option>
      {% endfor %}
    </select>
    <span class="installed-count" id="installed-count"></span>
    <span class="view-toggle" data-view-toggle data-key="civitai-installed-view" data-grid="installed-grid" data-table="installed-table">
      <button type="button" data-view="grid" title="Card view">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"></rect><rect x="14" y="3" width="7" height="7"></rect><rect x="14" y="14" width="7" height="7"></rect><rect x="3" y="14" width="7" height="7"></rect></svg>
      </button>
      <button type="button" data-view="table" title="Table view">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="6" x2="21" y2="6"></line><line x1="8" y1="12" x2="21" y2="12"></line><line x1="8" y1="18" x2="21" y2="18"></line><line x1="3" y1="6" x2="3.01" y2="6"></line><line x1="3" y1="12" x2="3.01" y2="12"></line><line x1="3" y1="18" x2="3.01" y2="18"></line></svg>
      </button>
    </span>
  </div>

  <div class="grid" id="installed-grid">
    {% for model in models %}
      {% include "_installed_card.html" %}
    {% endfor %}
    <p class="installed-empty" id="installed-empty-grid" style="grid-column: 1/-1;" hidden>No installed models match this filter.</p>
  </div>
  <table class="installed-table" id="installed-table" style="display:none;">
    <thead>
      <tr>
        <th><button type="button" class="sort-btn" data-sort-key="name">Name <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg></button></th>
        <th><button type="button" class="sort-btn" data-sort-key="type">Type <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg></button></th>
        <th><button type="button" class="sort-btn" data-sort-key="base">Base <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg></button></th>
        <th><button type="button" class="sort-btn" data-sort-key="path">Path <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg></button></th>
      </tr>
    </thead>
    <tbody id="installed-body">
      {% for model in models %}
      <tr data-installed-row data-row-href="/installed/{{ model.path_hash }}" style="cursor:pointer;"
          data-name="{{ model.get('name', '—') }}"
          data-type="{{ model.get('type', '—') }}"
          data-base="{{ model.get('base', '—') }}"
          data-path="{{ model.get('path', '—') }}">
        <td class="installed-table__name">{{ model.get('name', '—') }}</td>
        <td><span class="installed-table__type">{{ model.get('type', '—') }}</span></td>
        <td class="installed-table__base">{{ model.get('base', '—') }}</td>
        <td class="installed-table__path">{{ model.get('path', '—') }}</td>
      </tr>
      {% endfor %}
      <tr id="installed-empty-row" hidden><td class="installed-empty" colspan="4">No installed models match this filter.</td></tr>
    </tbody>
  </table>
</div>
{% else %}
<p class="empty">No models installed yet.</p>
{% endif %}
</div>
{% endblock %}
```

- [ ] **Step 2: Rewrite `initInstalledTable` in `app.js`**

Replace the entire `initInstalledTable` function (from `function initInstalledTable() {` through its closing `}` right before `function init() {`) with:

```javascript
  // ---- installed page: client-side sort + filter over server-rendered cards/rows ----
  function initInstalledTable() {
    var root = document.getElementById("installed-root");
    if (!root) return;

    var filterInput = document.getElementById("installed-filter");
    var typeSelect = document.getElementById("installed-type-filter");
    var countEl = document.getElementById("installed-count");
    var tableBody = document.getElementById("installed-body");
    var gridEl = document.getElementById("installed-grid");
    var sortButtons = root.querySelectorAll(".sort-btn");
    var emptyCard = document.getElementById("installed-empty-grid");
    var emptyRow = document.getElementById("installed-empty-row");

    var cardEls = Array.prototype.filter.call(
      gridEl.querySelectorAll("[data-installed-row]"),
      function (el) { return el !== emptyCard; }
    );
    var rowEls = Array.prototype.filter.call(
      tableBody.querySelectorAll("[data-installed-row]"),
      function (el) { return el !== emptyRow; }
    );

    var rows = cardEls.map(function (cardEl, i) {
      return {
        card: cardEl,
        row: rowEls[i],
        name: cardEl.dataset.name,
        type: cardEl.dataset.type,
        base: cardEl.dataset.base,
        path: cardEl.dataset.path,
      };
    });
    var total = rows.length;
    var sort = { key: "name", dir: 1 };

    function render() {
      var filterText = filterInput.value.toLowerCase();
      var typeFilter = typeSelect.value;
      var visible = rows.filter(function (r) {
        var matchesText = !filterText || r.name.toLowerCase().indexOf(filterText) !== -1 || r.path.toLowerCase().indexOf(filterText) !== -1;
        var matchesType = !typeFilter || r.type === typeFilter;
        return matchesText && matchesType;
      });
      visible.sort(function (a, b) { return a[sort.key].localeCompare(b[sort.key]) * sort.dir; });

      rows.forEach(function (r) {
        r.card.hidden = true;
        r.row.hidden = true;
      });
      visible.forEach(function (r) {
        r.card.hidden = false;
        r.row.hidden = false;
        gridEl.appendChild(r.card);
        tableBody.appendChild(r.row);
      });
      gridEl.appendChild(emptyCard);
      tableBody.appendChild(emptyRow);
      emptyCard.hidden = visible.length !== 0;
      emptyRow.hidden = visible.length !== 0;

      countEl.textContent = visible.length + " of " + total + " installed";

      sortButtons.forEach(function (btn) {
        var isActive = btn.dataset.sortKey === sort.key;
        btn.classList.toggle("is-active", isActive);
        var svg = btn.querySelector("svg");
        if (svg) svg.classList.toggle("is-desc", isActive && sort.dir === -1);
      });
    }

    filterInput.addEventListener("input", render);
    typeSelect.addEventListener("change", render);
    sortButtons.forEach(function (btn) {
      btn.addEventListener("click", function () {
        var key = btn.dataset.sortKey;
        if (sort.key === key) sort.dir *= -1;
        else { sort.key = key; sort.dir = 1; }
        render();
      });
    });

    render();
  }
```

Note: the `[data-row-href]` delegated click handler at the bottom of `app.js` already navigates any element with that attribute (used today by Browse's table rows) — it needs no changes to also handle the Installed table's `<tr data-row-href>` rows added in Step 1. The Installed grid's cards are plain `<a href>` elements, so they navigate natively without any JS.

- [ ] **Step 3: Manual verification in a browser**

Against a live pod (or any environment where `civitai-manager` can run against a real or stubbed InvokeAI — if fully offline, at minimum verify no JS console errors on page load with an empty `models` list):

1. Load `/installed`. Confirm cards render in the grid with thumbnails/badges (for models with sidecars) and plain cards (for models without).
2. Type into the filter box — confirm both grid and table narrow correctly and the count updates.
3. Select a type from the dropdown — confirm filtering by type works.
4. Click a column header in table view — confirm sort toggles ascending/descending and the arrow icon flips.
5. Switch to table view (view toggle) — confirm rows are clickable (cursor pointer, navigates to `/installed/{hash}` on click).
6. Filter to zero results — confirm the "No installed models match this filter" message appears in both grid and table views, and disappears again when the filter is cleared.
7. Click a card — confirm it navigates to `/installed/{path_hash}` and the detail page (Task 5) renders.

- [ ] **Step 4: Commit**

```bash
git add civitai_manager/templates/installed.html civitai_manager/static/app.js
git commit -m "$(cat <<'EOF'
feat: server-render Installed grid/table, mirror Browse's card UX

Cards are now Jinja-rendered (badge, installed version, distinguishing
border) instead of built from scratch in JS. Filter/sort now operate
on the real server-rendered DOM nodes (hide + reorder) instead of
rebuilding them from hidden sentinel spans.
EOF
)"
```

---

### Task 7: Final end-to-end verification

**Files:** none (verification only)

- [ ] **Step 1: Full manual walkthrough against a live pod**

Per the spec's Testing Notes:

1. Install a model from `/browse`'s detail page.
2. Wait for the install to complete (watch the stamp go to `INSTALLED`, or check Server Admin's Logs page for `civitai-manager`).
3. Confirm a sidecar appears: `ls /workspace/civitai-metadata/`.
4. Go to `/installed` — confirm the new model's card shows the "INSTALLED" badge and its version label.
5. Click the card — confirm the detail page shows full CivitAI metadata (description, creator, stats, license) plus the local install info panel (path/type/base), and "View on CivitAI" links to the correct model page.
6. Find (or manually place) a model with no sidecar — confirm it still renders on `/installed` (no badge/version, "local model" label) and its detail page shows the "No CivitAI metadata available" message instead of a broken sidebar.
7. Repeat step 1 but **navigate away from the model's detail page immediately after clicking Install**, before the install finishes. Wait for the install to actually complete (check Server Admin logs), then reload `/installed` — confirm the sidecar was still captured despite navigating away, proving the background task in Task 2 is working as designed.

- [ ] **Step 2: Confirm no regressions on `/browse` and `/downloads`**

Since `app.js`'s shared `[data-row-href]` handler and `[data-view-toggle]` logic weren't touched beyond the Installed-specific function, spot-check:

1. `/browse` — search, grid/table toggle, pagination, clicking a result still work.
2. `/downloads` — page still loads and install-from-download still works.

- [ ] **Step 3: Update `CLAUDE.md`'s CivitAI Manager section**

Add a short paragraph to the existing "CivitAI Manager (port 8000)" section of `/Users/thomasspitznas/Projects/runpod-stability-matrix/CLAUDE.md`, after the existing paragraph about the Downloads feature's sidecar format:

```markdown
**Installed page metadata** (`civitai_manager/metadata_store.py`): a second, independent sidecar mechanism from the Downloads feature above — installing a model via the "Install" button (not "Download to folder") captures CivitAI metadata to `/workspace/civitai-metadata/<sha256(install_path)>.json`, written by a server-side background task (`main._track_install_metadata`) that polls the InvokeAI install job independently of the client, so it isn't lost if the browser tab navigates away mid-install. `/installed` and `/installed/{path_hash}` read these sidecars to give installed models the same clickable-card-plus-detail-page experience as Browse; models without a sidecar (installed outside the app, or before this feature existed) still render, just without CivitAI metadata.
```

- [ ] **Step 4: Commit the CLAUDE.md update**

```bash
git add CLAUDE.md
git commit -m "docs: document Installed page metadata sidecar mechanism"
```

---

## Self-Review Notes

- **Spec coverage:** Architecture/data-flow (Tasks 2–3), data model (Task 2's `_build_sidecar_metadata`), server-side completion tracking amendment (Task 2), component/page design for card + detail (Tasks 4–5), grid/table server-rendering (Task 6), error handling for missing/malformed sidecars (Task 1's `read_sidecar`, Task 4/5's `None`-safe templates), testing notes (Task 7) — all covered.
- **Known open risk carried forward from the spec:** `_extract_installed_path`'s field-name guesses are unverified against a live InvokeAI server; Task 2 Step 7 is the mandatory live-verification gate before this feature can be considered done, not optional polish.
- **Type/name consistency check:** `metadata_store.path_hash`/`write_sidecar`/`read_sidecar` (Task 1) are the exact names used in Tasks 2, 3, and 4. `_build_sidecar_metadata` and `_extract_installed_path` (Task 2) are the exact names referenced in Task 2's own wiring step and smoke test. Sidecar dict keys (`model_name`, `creator_username`, `installed_version_id`, `installed_version_name`, `versions`, `civitai_url`, `stats`, `allowCommercialUse`, `allowDerivatives`, `nsfw`, `publishedAt`, `tags`, `trigger_words`, `description`) are consistent between `_build_sidecar_metadata` (Task 2), `_installed_card.html` (Task 4), and `installed_detail.html` (Task 5).
