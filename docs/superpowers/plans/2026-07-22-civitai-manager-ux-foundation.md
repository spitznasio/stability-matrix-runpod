# CivitAI Manager Phase 1 UX Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix ~10 swallowed-error call sites, make background metadata-capture failures visible in the UI, add loading states and a Downloads detail page, and close several workflow-consistency gaps in CivitAI Manager (`civitai_manager/`, port 8000) — without changing its visual identity.

**Architecture:** A new `errors.py` helper classifies `httpx` exceptions into distinct user-facing messages, reused at every existing except-block. A second sidecar mechanism in `metadata_store.py` (`.error.json`, separate from the main `.json` metadata sidecar) persists background-task failures and surfaces them as a dismissible badge on the Installed page. A small OOB-swap toast component confirms the dismiss action. Downloads gains a real per-file detail page (reusing the sidecar and gallery route that already exist) instead of a bare table. See the design spec for full rationale: `docs/superpowers/specs/2026-07-22-civitai-manager-ux-foundation-design.md`.

**Tech Stack:** FastAPI, Jinja2, htmx 1.9.12, vanilla JS (no build step) — no new dependencies.

## Global Constraints

- No test framework exists or is introduced (`civitai_manager/` has zero `test_*.py` files). Verify pure functions via `python3 -c` smoke checks; verify routes/templates manually via curl/browser, per this repo's established pattern (`docs/superpowers/plans/2026-07-19-installed-page-mirror.md`).
- Reuse existing patterns exactly: the sidecar `path_hash` scheme (`metadata_store.py`), the OOB-swap pattern (`_version_update.html`), existing component classes (`.stamp`, `.btn`, `.chip`, `.card`, `.error-banner`). Do not invent parallel mechanisms.
- Existing code conventions: `dict | None` type hints, `httpx.HTTPError` catch-and-log-warning around upstream calls, `Path` from `pathlib`, minimal comments (one-liners for non-obvious "why" only).
- No visual identity change — reuse existing design tokens (`--accent`, `--ok`, `--danger`, `--surface`, `--border-strong`, `--font-mono`) for every new style.
- Loading states use htmx's built-in `.htmx-request` class (automatically added to the element that issued the request) rather than explicit `hx-indicator` elements — every case in this plan (a form, an anchor, a self-triggering div) already receives that class directly, so no template changes are needed for Task 6, only CSS.
- The Installed page's slow-job hint (Task 10) is implemented entirely client-side (a JS-side map keyed by the status fragment's stable element id, which is the same string across every poll swap) rather than threading a timestamp through the server — simpler, and avoids extra query-string plumbing for a purely cosmetic nudge.

---

## File Structure

- **Create:** `civitai_manager/errors.py` — `summarize_upstream_error()`
- **Create:** `civitai_manager/templates/_toast.html` — toast macro
- **Create:** `civitai_manager/templates/download_detail.html` — Downloads detail page
- **Modify:** `civitai_manager/main.py` — error wiring, retry-capable polling routes, background-error wiring, badge/dismiss route, clear-filters context, download detail route, `thumbnail_url` handling
- **Modify:** `civitai_manager/metadata_store.py` — `write_background_error`/`read_background_error`/`clear_background_error`
- **Modify:** `civitai_manager/templates/_gallery.html`, `_install_status.html`, `_download_status.html` — error/retry states
- **Modify:** `civitai_manager/templates/_installed_card.html`, `installed_detail.html` — background-error badge, static-tab visual cue, `back_url`
- **Modify:** `civitai_manager/templates/base.html` — `#toast-region`
- **Modify:** `civitai_manager/templates/browse_results.html` — clear-filters chip
- **Modify:** `civitai_manager/templates/_install_panel.html` — `thumbnail_url` hidden field
- **Modify:** `civitai_manager/templates/downloads.html`, `installed.html` — thumbnail column, `data-path-hash`
- **Modify:** `civitai_manager/static/style.css` — all new component/state styles
- **Modify:** `civitai_manager/static/app.js` — toast auto-dismiss, filter debounce, slow-job hint, filter-state URL sync
- **Modify:** `CLAUDE.md` — document the new mechanisms

---

### Task 1: Error-summary helper, wired into every swallowed catch site

**Files:**
- Create: `civitai_manager/errors.py`
- Modify: `civitai_manager/main.py`

**Interfaces:**
- Produces: `errors.summarize_upstream_error(exc: httpx.HTTPError, service: str) -> str`

- [ ] **Step 1: Write `civitai_manager/errors.py`**

```python
import httpx


def summarize_upstream_error(exc: httpx.HTTPError, service: str) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        message = f"{service} rejected the request (HTTP {exc.response.status_code})"
        try:
            body = exc.response.json()
            detail = body.get("detail") or body.get("error") or body.get("message")
        except (ValueError, AttributeError):
            detail = None
        if detail:
            message += f": {detail}"
        return message
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return f"Could not reach {service} — check that it's running."
    if isinstance(exc, httpx.TimeoutException):
        return f"{service} timed out responding."
    return f"{service} request failed: {exc}"
```

- [ ] **Step 2: Smoke-test it**

```bash
cd /Users/thomasspitznas/Projects/runpod-stability-matrix && python3 -c "
import httpx
from civitai_manager.errors import summarize_upstream_error

request = httpx.Request('POST', 'http://x/install')

response = httpx.Response(400, request=request, json={'detail': 'bad source'})
exc = httpx.HTTPStatusError('bad', request=request, response=response)
assert summarize_upstream_error(exc, 'InvokeAI') == 'InvokeAI rejected the request (HTTP 400): bad source'

response2 = httpx.Response(503, request=request, text='not json')
exc2 = httpx.HTTPStatusError('bad', request=request, response=response2)
assert summarize_upstream_error(exc2, 'InvokeAI') == 'InvokeAI rejected the request (HTTP 503)'

assert summarize_upstream_error(httpx.ConnectError('refused'), 'InvokeAI') == \"Could not reach InvokeAI — check that it's running.\"
assert summarize_upstream_error(httpx.ConnectTimeout('timeout'), 'aria2') == \"Could not reach aria2 — check that it's running.\"
assert summarize_upstream_error(httpx.ReadTimeout('slow'), 'CivitAI') == 'CivitAI timed out responding.'
assert summarize_upstream_error(httpx.HTTPError('aria2 RPC error calling aria2.addUri: bad gid'), 'the download daemon') == 'the download daemon request failed: aria2 RPC error calling aria2.addUri: bad gid'

print('OK')
"
```

Expected: `OK` printed.

- [ ] **Step 3: Import it in `main.py`**

Replace:
```python
from . import config, downloads, metadata_store
from .aria2_client import Aria2Client
from .aria2_client import TERMINAL_STATUSES as ARIA2_TERMINAL_STATUSES
from .civitai_client import CivitAIClient
from .formatting import format_commercial_use
from .invokeai_client import InvokeAIClient
from .sanitize import html_to_text
```
With:
```python
from . import config, downloads, metadata_store
from .aria2_client import Aria2Client
from .aria2_client import TERMINAL_STATUSES as ARIA2_TERMINAL_STATUSES
from .civitai_client import CivitAIClient
from .errors import summarize_upstream_error
from .formatting import format_commercial_use
from .invokeai_client import InvokeAIClient
from .sanitize import html_to_text
```

- [ ] **Step 4: Wire into `POST /install`**

Replace:
```python
    except httpx.HTTPError:
        logger.warning("Install request rejected by InvokeAI for %s", download_url, exc_info=True)
        return render_error(
            request,
            "InvokeAI is not ready yet, or the install request was rejected — try again shortly.",
        )
```
With:
```python
    except httpx.HTTPError as exc:
        logger.warning("Install request rejected by InvokeAI for %s", download_url, exc_info=True)
        return render_error(request, summarize_upstream_error(exc, "InvokeAI"))
```

- [ ] **Step 5: Wire into `POST /downloads/{filename}/install`**

Replace:
```python
    except httpx.HTTPError:
        logger.warning("Install request rejected by InvokeAI for %s", target, exc_info=True)
        return render_error(
            request,
            "InvokeAI is not ready yet, or the install request was rejected — try again shortly.",
        )
```
With:
```python
    except httpx.HTTPError as exc:
        logger.warning("Install request rejected by InvokeAI for %s", target, exc_info=True)
        return render_error(request, summarize_upstream_error(exc, "InvokeAI"))
```

- [ ] **Step 6: Wire into `POST /download`**

Replace:
```python
    except httpx.HTTPError:
        logger.warning("aria2 daemon unreachable queueing download for %s", filename, exc_info=True)
        return render_error(
            request,
            "The download daemon is not reachable right now — try again shortly.",
        )
```
With:
```python
    except httpx.HTTPError as exc:
        logger.warning("aria2 daemon unreachable queueing download for %s", filename, exc_info=True)
        return render_error(request, summarize_upstream_error(exc, "the download daemon"))
```

- [ ] **Step 7: Wire into the global handler's fallback**

Replace:
```python
    return render_error(request, f"Upstream request failed: {exc}", status_code=502)
```
With:
```python
    return render_error(request, summarize_upstream_error(exc, "CivitAI"), status_code=502)
```

- [ ] **Step 8: Fix the fully-silent gallery fetch**

Replace:
```python
@app.get("/models/{model_id}/versions/{version_id}/gallery", response_class=HTMLResponse)
async def version_gallery(request: Request, model_id: int, version_id: int, refresh: bool = False):
    # Lazily enriches a version's thumbnails with generation metadata (prompt,
    # sampler, etc.) — only fetched once a version is actually expanded, since
    # fetching this for every version up front doesn't scale to models with
    # dozens of versions (one extra request per version).
    try:
        images = await request.app.state.civitai.get_version_images(version_id, refresh=refresh)
    except httpx.HTTPError:
        images = []
    return templates.TemplateResponse(request, "_gallery.html", {"images": images})
```
With:
```python
@app.get("/models/{model_id}/versions/{version_id}/gallery", response_class=HTMLResponse)
async def version_gallery(request: Request, model_id: int, version_id: int, refresh: bool = False):
    # Lazily enriches a version's thumbnails with generation metadata (prompt,
    # sampler, etc.) — only fetched once a version is actually expanded, since
    # fetching this for every version up front doesn't scale to models with
    # dozens of versions (one extra request per version).
    try:
        images = await request.app.state.civitai.get_version_images(version_id, refresh=refresh)
    except httpx.HTTPError as exc:
        logger.warning(
            "Failed to load gallery for model_id=%s version_id=%s", model_id, version_id, exc_info=True
        )
        return templates.TemplateResponse(
            request, "_gallery.html",
            {
                "images": [], "error": summarize_upstream_error(exc, "CivitAI"),
                "retry_url": f"/models/{model_id}/versions/{version_id}/gallery",
            },
        )
    return templates.TemplateResponse(request, "_gallery.html", {"images": images, "error": None})
```

- [ ] **Step 9: Add the retry state to `_gallery.html`**

Replace the full contents of `civitai_manager/templates/_gallery.html`:
```html
{% for image in images[:24] %}
<button type="button" class="thumb"
  data-url="{{ image.url }}"
  data-prompt="{{ image.meta.prompt if image.meta else '' }}"
  data-negative="{{ image.meta.negativePrompt if image.meta else '' }}"
  data-sampler="{{ image.meta.sampler if image.meta else '' }}"
  data-steps="{{ image.meta.steps if image.meta else '' }}"
  data-cfg="{{ image.meta.cfgScale if image.meta else '' }}"
  data-seed="{{ image.meta.seed if image.meta else '' }}"
>
  <img src="{{ image.url }}" alt="" loading="lazy">
  {% if image.meta %}<span class="thumb__badge">i</span>{% endif %}
</button>
{% endfor %}
```
With:
```html
{% if error %}
<p class="empty">{{ error }} <button type="button" class="btn btn--small" hx-get="{{ retry_url }}" hx-target="closest .gallery" hx-swap="innerHTML">Retry</button></p>
{% else %}
{% for image in images[:24] %}
<button type="button" class="thumb"
  data-url="{{ image.url }}"
  data-prompt="{{ image.meta.prompt if image.meta else '' }}"
  data-negative="{{ image.meta.negativePrompt if image.meta else '' }}"
  data-sampler="{{ image.meta.sampler if image.meta else '' }}"
  data-steps="{{ image.meta.steps if image.meta else '' }}"
  data-cfg="{{ image.meta.cfgScale if image.meta else '' }}"
  data-seed="{{ image.meta.seed if image.meta else '' }}"
>
  <img src="{{ image.url }}" alt="" loading="lazy">
  {% if image.meta %}<span class="thumb__badge">i</span>{% endif %}
</button>
{% endfor %}
{% endif %}
```

- [ ] **Step 10: Manual verification**

Against a running app (`uvicorn civitai_manager.main:app --host 0.0.0.0 --port 8000`):
1. Stop InvokeAI. Click Install on a model. Confirm the message reads `Could not reach InvokeAI — check that it's running.` (not the old generic string).
2. Restart InvokeAI, submit an install with a deliberately malformed `download_url` (edit the form via devtools, or just confirm the HTTP-status branch works via curl: `curl -s -X POST http://localhost:8000/install -d 'download_url=not-a-url'` and check the rendered message mentions an HTTP status).
3. Stop the `aria2-rpc` service (Server Admin dashboard or `python3 -m server_admin.supervisor stop aria2-rpc`). Try "Download to folder". Confirm the aria2-specific message.
4. Load a model detail page; confirm the gallery still renders normally when CivitAI is reachable.

- [ ] **Step 11: Commit**

```bash
git add civitai_manager/errors.py civitai_manager/main.py civitai_manager/templates/_gallery.html
git commit -m "$(cat <<'EOF'
feat: distinguish upstream error messages instead of one generic string

summarize_upstream_error() classifies httpx exceptions (connect
failure, timeout, HTTP status with parsed detail) so InvokeAI/aria2/
CivitAI failures surface distinct, actionable messages. Also fixes
the gallery fetch's fully-silent failure (no logging, blank gallery)
to log and offer a retry.
EOF
)"
```

---

### Task 2: Bounded-retry install/download status polling

**Files:**
- Modify: `civitai_manager/main.py`
- Modify: `civitai_manager/templates/_install_status.html`, `civitai_manager/templates/_download_status.html`

**Interfaces:**
- Consumes: `summarize_upstream_error` (Task 1)
- Produces: `MAX_STATUS_POLL_ERRORS` constant in `main.py`

Today, a transient error on `GET /install/{job_id}/status` or `GET /download/{gid}/status` makes the returned fragment omit its `hx-get`/`hx-trigger` attributes entirely — the poll silently stops forever, with no way to resume short of reloading the page. This task keeps retrying for a bounded number of attempts, then falls back to a fragment with a manual Retry button.

- [ ] **Step 1: Add the retry-count constant**

Replace:
```python
TERMINAL_STATUSES = {"completed", "error", "cancelled"}
```
With:
```python
TERMINAL_STATUSES = {"completed", "error", "cancelled"}
MAX_STATUS_POLL_ERRORS = 5
```

- [ ] **Step 2: Rewrite `GET /install/{job_id}/status`**

Replace:
```python
@app.get("/install/{job_id}/status", response_class=HTMLResponse)
async def install_status(request: Request, job_id: str):
    try:
        job = await request.app.state.invokeai.get_install_job(job_id)
    except httpx.HTTPError:
        logger.warning("Lost contact with InvokeAI polling install job %s", job_id, exc_info=True)
        return render_error(request, "Lost contact with InvokeAI while checking install status.")
    if job.get("status") in TERMINAL_STATUSES:
        logger.info("Install job %s reached terminal status %s", job_id, job.get("status"))
    return templates.TemplateResponse(
        request,
        "_install_status.html",
        {"job": job, "terminal": job.get("status") in TERMINAL_STATUSES},
    )
```
With:
```python
@app.get("/install/{job_id}/status", response_class=HTMLResponse)
async def install_status(request: Request, job_id: str, error_streak: int = 0):
    try:
        job = await request.app.state.invokeai.get_install_job(job_id)
    except httpx.HTTPError as exc:
        error_streak += 1
        logger.warning(
            "Lost contact with InvokeAI polling install job %s (streak=%s)", job_id, error_streak, exc_info=True
        )
        return templates.TemplateResponse(
            request, "_install_status.html",
            {
                "job": {"id": job_id, "status": "unknown"},
                "terminal": False,
                "poll_error": summarize_upstream_error(exc, "InvokeAI"),
                "error_streak": error_streak,
                "can_retry": error_streak >= MAX_STATUS_POLL_ERRORS,
            },
        )
    if job.get("status") in TERMINAL_STATUSES:
        logger.info("Install job %s reached terminal status %s", job_id, job.get("status"))
    return templates.TemplateResponse(
        request, "_install_status.html",
        {"job": job, "terminal": job.get("status") in TERMINAL_STATUSES, "poll_error": None, "error_streak": 0, "can_retry": False},
    )
```

- [ ] **Step 3: Rewrite `GET /download/{gid}/status`**

Replace:
```python
@app.get("/download/{gid}/status", response_class=HTMLResponse)
async def download_status(request: Request, gid: str):
    try:
        job = await request.app.state.aria2.tell_status(gid)
    except httpx.HTTPError:
        logger.warning("Lost contact with aria2 polling gid=%s", gid, exc_info=True)
        return render_error(request, "Lost contact with the download daemon while checking status.")
    if job.get("status") in ARIA2_TERMINAL_STATUSES:
        logger.info("Download gid=%s reached terminal status %s", gid, job.get("status"))
        await request.app.state.aria2.cleanup_control_file(gid)
    return templates.TemplateResponse(
        request,
        "_download_status.html",
        {"job": job, "terminal": job.get("status") in ARIA2_TERMINAL_STATUSES},
    )
```
With:
```python
@app.get("/download/{gid}/status", response_class=HTMLResponse)
async def download_status(request: Request, gid: str, error_streak: int = 0):
    try:
        job = await request.app.state.aria2.tell_status(gid)
    except httpx.HTTPError as exc:
        error_streak += 1
        logger.warning(
            "Lost contact with aria2 polling gid=%s (streak=%s)", gid, error_streak, exc_info=True
        )
        return templates.TemplateResponse(
            request, "_download_status.html",
            {
                "job": {"gid": gid, "status": "unknown"},
                "terminal": False,
                "poll_error": summarize_upstream_error(exc, "the download daemon"),
                "error_streak": error_streak,
                "can_retry": error_streak >= MAX_STATUS_POLL_ERRORS,
            },
        )
    if job.get("status") in ARIA2_TERMINAL_STATUSES:
        logger.info("Download gid=%s reached terminal status %s", gid, job.get("status"))
        await request.app.state.aria2.cleanup_control_file(gid)
    return templates.TemplateResponse(
        request, "_download_status.html",
        {"job": job, "terminal": job.get("status") in ARIA2_TERMINAL_STATUSES, "poll_error": None, "error_streak": 0, "can_retry": False},
    )
```

(`poll_error`/`error_streak`/`can_retry` are not passed by `POST /install`/`POST /download`/`POST /downloads/{filename}/install`'s own responses — Jinja2's default `Undefined` is falsy in `{% if %}`, so those templates render fine without every call site being updated.)

- [ ] **Step 4: Update `_install_status.html`**

Replace the full contents of `civitai_manager/templates/_install_status.html`:
```html
<div
  id="install-job-{{ job.get('id', 'pending') }}"
  class="install-status"
  {% if not terminal %}
  hx-get="/install/{{ job.get('id') }}/status"
  hx-trigger="load delay:2s, every 2s [document.visibilityState=='visible']"
  hx-swap="outerHTML"
  {% endif %}
>
  <span class="install-status__line">Install: {{ job.get('status', 'pending') }}</span>
  {% if job.get('bytes') and job.get('total_bytes') %}
  <span class="install-status__bytes">{{ job.bytes }} / {{ job.total_bytes }} bytes</span>
  {% endif %}
  {% if terminal and job.get('status') == 'completed' %}
  <span class="stamp stamp--ok stamp--thunk">INSTALLED</span>
  {% elif terminal %}
  <span class="stamp stamp--danger">{{ job.get('error_type', 'INSTALL FAILED') }}: {{ job.get('error', '') }}</span>
  {% endif %}
</div>
```
With:
```html
<div
  id="install-job-{{ job.get('id', 'pending') }}"
  class="install-status"
  {% if not terminal and not can_retry %}
  hx-get="/install/{{ job.get('id') }}/status{% if error_streak %}?error_streak={{ error_streak }}{% endif %}"
  hx-trigger="load delay:2s, every 2s [document.visibilityState=='visible']"
  hx-swap="outerHTML"
  {% endif %}
>
  <span class="install-status__line">Install: {{ job.get('status', 'pending') }}</span>
  {% if job.get('bytes') and job.get('total_bytes') %}
  <span class="install-status__bytes">{{ job.bytes }} / {{ job.total_bytes }} bytes</span>
  {% endif %}
  {% if poll_error %}
  <span class="install-status__poll-error">{{ poll_error }}</span>
  {% if can_retry %}
  <button type="button" class="btn btn--small" hx-get="/install/{{ job.get('id') }}/status" hx-target="closest .install-status" hx-swap="outerHTML">Retry</button>
  {% endif %}
  {% endif %}
  {% if terminal and job.get('status') == 'completed' %}
  <span class="stamp stamp--ok stamp--thunk">INSTALLED</span>
  {% elif terminal %}
  <span class="stamp stamp--danger">{{ job.get('error_type', 'INSTALL FAILED') }}: {{ job.get('error', '') }}</span>
  {% endif %}
</div>
```

- [ ] **Step 5: Update `_download_status.html`**

Replace the full contents of `civitai_manager/templates/_download_status.html`:
```html
<div
  id="download-job-{{ job.get('gid', 'pending') }}"
  class="install-status"
  {% if not terminal %}
  hx-get="/download/{{ job.get('gid') }}/status"
  hx-trigger="load delay:2s, every 2s [document.visibilityState=='visible']"
  hx-swap="outerHTML"
  {% endif %}
>
  <span class="install-status__line">Download: {{ job.get('status', 'pending') }}</span>
  {% if job.get('completedLength') and job.get('totalLength') %}
  <span class="install-status__bytes">{{ job.completedLength }} / {{ job.totalLength }} bytes{% if job.get('downloadSpeed') %} &middot; {{ (job.downloadSpeed | int // 1024) }} KB/s{% endif %}</span>
  {% endif %}
  {% if terminal and job.get('status') == 'complete' %}
  <span class="stamp stamp--ok stamp--thunk">DOWNLOADED</span>
  {% elif terminal %}
  <span class="stamp stamp--danger">DOWNLOAD FAILED: {{ job.get('errorMessage', '') }}</span>
  {% endif %}
</div>
```
With:
```html
<div
  id="download-job-{{ job.get('gid', 'pending') }}"
  class="install-status"
  {% if not terminal and not can_retry %}
  hx-get="/download/{{ job.get('gid') }}/status{% if error_streak %}?error_streak={{ error_streak }}{% endif %}"
  hx-trigger="load delay:2s, every 2s [document.visibilityState=='visible']"
  hx-swap="outerHTML"
  {% endif %}
>
  <span class="install-status__line">Download: {{ job.get('status', 'pending') }}</span>
  {% if job.get('completedLength') and job.get('totalLength') %}
  <span class="install-status__bytes">{{ job.completedLength }} / {{ job.totalLength }} bytes{% if job.get('downloadSpeed') %} &middot; {{ (job.downloadSpeed | int // 1024) }} KB/s{% endif %}</span>
  {% endif %}
  {% if poll_error %}
  <span class="install-status__poll-error">{{ poll_error }}</span>
  {% if can_retry %}
  <button type="button" class="btn btn--small" hx-get="/download/{{ job.get('gid') }}/status" hx-target="closest .install-status" hx-swap="outerHTML">Retry</button>
  {% endif %}
  {% endif %}
  {% if terminal and job.get('status') == 'complete' %}
  <span class="stamp stamp--ok stamp--thunk">DOWNLOADED</span>
  {% elif terminal %}
  <span class="stamp stamp--danger">DOWNLOAD FAILED: {{ job.get('errorMessage', '') }}</span>
  {% endif %}
</div>
```

- [ ] **Step 6: Add the error-text style**

In `civitai_manager/static/style.css`, in the "Install/download status" section, replace:
```css
.install-status { margin-top: 0.5rem; display: flex; align-items: center; gap: 0.75rem; font-family: var(--font-mono); font-size: 0.82rem; flex-wrap: wrap; }
.install-status__bytes { color: var(--text-dim); }
```
With:
```css
.install-status { margin-top: 0.5rem; display: flex; align-items: center; gap: 0.75rem; font-family: var(--font-mono); font-size: 0.82rem; flex-wrap: wrap; }
.install-status__bytes { color: var(--text-dim); }
.install-status__poll-error { color: var(--danger); }
.install-status__slow-hint { color: var(--text-dim); font-style: italic; }
```

(`.install-status__slow-hint` is unused until Task 10 — added now so the two touches to this CSS block don't conflict.)

- [ ] **Step 7: Manual verification**

1. Start an install. While it's pending, stop InvokeAI. Confirm the status line shows the "Could not reach InvokeAI" message and keeps auto-retrying (watch the network tab: repeated `GET /install/{id}/status?error_streak=N` requests every 2s, N incrementing).
2. Leave InvokeAI stopped through 5 failed polls (~10s). Confirm auto-polling stops and a "Retry" button appears.
3. Restart InvokeAI, click Retry. Confirm polling resumes and eventually reaches `INSTALLED`.
4. Repeat 1–3 for a download, stopping `aria2-rpc` instead.

- [ ] **Step 8: Commit**

```bash
git add civitai_manager/main.py civitai_manager/templates/_install_status.html civitai_manager/templates/_download_status.html civitai_manager/static/style.css
git commit -m "$(cat <<'EOF'
feat: retry install/download status polling before giving up

A transient upstream error used to freeze the polling fragment
permanently (hx-get was simply omitted). Now it retries up to
MAX_STATUS_POLL_ERRORS times before falling back to a manual
Retry button.
EOF
)"
```

---

### Task 3: Background-error sidecar, wired into the metadata-capture tasks

**Files:**
- Modify: `civitai_manager/metadata_store.py`
- Modify: `civitai_manager/main.py`

**Interfaces:**
- Produces: `metadata_store.write_background_error(model_path: str, message: str) -> None`, `metadata_store.read_background_error(model_path: str) -> dict | None`, `metadata_store.clear_background_error(model_path: str) -> None`

- [ ] **Step 1: Add the background-error sidecar functions**

Replace the full contents of `civitai_manager/metadata_store.py`:
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
With:
```python
import hashlib
import json
import logging
from datetime import datetime, timezone
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


def _error_path(model_path: str) -> Path:
    # Separate suffix from _sidecar_path's .json — a background-task failure
    # must never be able to overwrite or block reading the real metadata.
    return Path(config.CIVITAI_METADATA_DIR) / f"{path_hash(model_path)}.error.json"


def write_background_error(model_path: str, message: str) -> None:
    target = _error_path(model_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"message": message, "occurred_at": datetime.now(timezone.utc).isoformat()}))


def read_background_error(model_path: str) -> dict | None:
    target = _error_path(model_path)
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text())
    except (OSError, json.JSONDecodeError):
        logger.warning("Failed to read background error %s", target, exc_info=True)
        return None


def clear_background_error(model_path: str) -> None:
    _error_path(model_path).unlink(missing_ok=True)
```

- [ ] **Step 2: Smoke-test the new functions**

```bash
cd /Users/thomasspitznas/Projects/runpod-stability-matrix && python3 -c "
import tempfile, os
os.environ['CIVITAI_METADATA_DIR'] = tempfile.mkdtemp()
from civitai_manager import metadata_store

p = '/workspace/invokeai/models/sd-1/main/foo.safetensors'
assert metadata_store.read_background_error(p) is None

metadata_store.write_background_error(p, 'Trigger words may not have saved.')
err = metadata_store.read_background_error(p)
assert err['message'] == 'Trigger words may not have saved.'
assert 'occurred_at' in err

# writing/reading a background error must not disturb the real sidecar
metadata_store.write_sidecar(p, {'model_name': 'Foo'})
assert metadata_store.read_sidecar(p) == {'model_name': 'Foo'}
assert metadata_store.read_background_error(p) is not None

metadata_store.clear_background_error(p)
assert metadata_store.read_background_error(p) is None
assert metadata_store.read_sidecar(p) == {'model_name': 'Foo'}, 'clearing the error must not touch the real sidecar'

metadata_store.clear_background_error(p)  # no-op on missing file, must not raise

print('OK')
"
```

Expected: `OK` printed.

- [ ] **Step 3: Wire into `_track_install_metadata`**

Replace:
```python
async def _track_install_metadata(app: FastAPI, job_id: str, model_id: int, version_id: int) -> None:
    invokeai: InvokeAIClient = app.state.invokeai
    civitai: CivitAIClient = app.state.civitai
    job = await _wait_for_completed_job(invokeai, job_id)
    if job is None:
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
```
With:
```python
async def _track_install_metadata(app: FastAPI, job_id: str, model_id: int, version_id: int) -> None:
    invokeai: InvokeAIClient = app.state.invokeai
    civitai: CivitAIClient = app.state.civitai
    job = await _wait_for_completed_job(invokeai, job_id)
    if job is None:
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
        metadata_store.write_background_error(
            installed_path, "Couldn't fetch CivitAI details after install — metadata is missing on this model's page."
        )
        return
    metadata_store.write_sidecar(installed_path, _build_sidecar_metadata(model, version_id))
    logger.info(
        "Captured install metadata for model_id=%s version_id=%s at %s",
        model_id, version_id, installed_path,
    )
```

- [ ] **Step 4: Wire into `_track_download_install`**

Replace:
```python
async def _track_download_install(
    app: FastAPI,
    job_id: str,
    civitai_model_id: int | None,
    civitai_version_id: int | None,
    trigger_words: list[str] | None,
    civitai_url: str | None,
) -> None:
    # Two follow-ups that only apply to installs started from the Downloads
    # page (POST /downloads/{filename}/install) — unlike the direct "Install"
    # button, that flow already has CivitAI metadata captured at download
    # time and passes it as install-time config overrides:
    #
    #   1. InvokeAI 6.13.6 silently drops trigger_phrases/source_url from
    #      that install-time config (confirmed empirically: a completed
    #      job's config_in carries the values we sent, config_out doesn't —
    #      name/description apply fine, only these two don't stick). Re-apply
    #      them via PATCH /models/i/{key} once the model record exists,
    #      which was confirmed to actually work. Harmless no-op for
    #      trigger_phrases on model types that don't support it (e.g.
    #      TextualInversion/embedding — InvokeAI just doesn't add the key).
    #   2. This install path was never wired into the Installed-page
    #      metadata sidecar capture that POST /install already gets via
    #      _track_install_metadata — do the same capture here.
    invokeai: InvokeAIClient = app.state.invokeai
    civitai: CivitAIClient = app.state.civitai
    job = await _wait_for_completed_job(invokeai, job_id)
    if job is None:
        return

    config_out = job.get("config_out") if isinstance(job.get("config_out"), dict) else None
    model_key = config_out.get("key") if config_out else None
    reapply = {k: v for k, v in {"trigger_phrases": trigger_words, "source_url": civitai_url}.items() if v}
    if model_key and reapply:
        try:
            await invokeai.update_model_config(model_key, reapply)
            logger.info("Re-applied %s on model %s after download-install", list(reapply), model_key)
        except httpx.HTTPError:
            logger.warning(
                "Failed to re-apply %s on model %s after download-install",
                list(reapply), model_key, exc_info=True,
            )

    if not (civitai_model_id and civitai_version_id):
        return
    installed_path = _extract_installed_path(job)
    if not installed_path:
        logger.warning(
            "Download-install job %s completed but no installed path found in job payload; "
            "skipping metadata capture. job=%s", job_id, job,
        )
        return
    try:
        model = await civitai.get_model(civitai_model_id)
    except httpx.HTTPError:
        logger.warning(
            "Failed to fetch CivitAI model %s for metadata capture after download-install",
            civitai_model_id, exc_info=True,
        )
        return
    metadata_store.write_sidecar(installed_path, _build_sidecar_metadata(model, civitai_version_id))
    logger.info(
        "Captured download-install metadata for model_id=%s version_id=%s at %s",
        civitai_model_id, civitai_version_id, installed_path,
    )
```
With:
```python
async def _track_download_install(
    app: FastAPI,
    job_id: str,
    civitai_model_id: int | None,
    civitai_version_id: int | None,
    trigger_words: list[str] | None,
    civitai_url: str | None,
) -> None:
    # Two follow-ups that only apply to installs started from the Downloads
    # page (POST /downloads/{filename}/install) — unlike the direct "Install"
    # button, that flow already has CivitAI metadata captured at download
    # time and passes it as install-time config overrides:
    #
    #   1. InvokeAI 6.13.6 silently drops trigger_phrases/source_url from
    #      that install-time config (confirmed empirically: a completed
    #      job's config_in carries the values we sent, config_out doesn't —
    #      name/description apply fine, only these two don't stick). Re-apply
    #      them via PATCH /models/i/{key} once the model record exists,
    #      which was confirmed to actually work. Harmless no-op for
    #      trigger_phrases on model types that don't support it (e.g.
    #      TextualInversion/embedding — InvokeAI just doesn't add the key).
    #   2. This install path was never wired into the Installed-page
    #      metadata sidecar capture that POST /install already gets via
    #      _track_install_metadata — do the same capture here.
    invokeai: InvokeAIClient = app.state.invokeai
    civitai: CivitAIClient = app.state.civitai
    job = await _wait_for_completed_job(invokeai, job_id)
    if job is None:
        return

    # Computed once, up front, so both follow-ups below can attach a
    # background error to the right model even if the metadata-capture
    # follow-up never runs (e.g. no civitai_model_id was passed).
    installed_path = _extract_installed_path(job)

    config_out = job.get("config_out") if isinstance(job.get("config_out"), dict) else None
    model_key = config_out.get("key") if config_out else None
    reapply = {k: v for k, v in {"trigger_phrases": trigger_words, "source_url": civitai_url}.items() if v}
    if model_key and reapply:
        try:
            await invokeai.update_model_config(model_key, reapply)
            logger.info("Re-applied %s on model %s after download-install", list(reapply), model_key)
        except httpx.HTTPError:
            logger.warning(
                "Failed to re-apply %s on model %s after download-install",
                list(reapply), model_key, exc_info=True,
            )
            if installed_path:
                metadata_store.write_background_error(
                    installed_path, "Trigger words/source link may not have saved to InvokeAI."
                )

    if not (civitai_model_id and civitai_version_id):
        return
    if not installed_path:
        logger.warning(
            "Download-install job %s completed but no installed path found in job payload; "
            "skipping metadata capture. job=%s", job_id, job,
        )
        return
    try:
        model = await civitai.get_model(civitai_model_id)
    except httpx.HTTPError:
        logger.warning(
            "Failed to fetch CivitAI model %s for metadata capture after download-install",
            civitai_model_id, exc_info=True,
        )
        metadata_store.write_background_error(
            installed_path, "Couldn't fetch CivitAI details after install — metadata is missing on this model's page."
        )
        return
    metadata_store.write_sidecar(installed_path, _build_sidecar_metadata(model, civitai_version_id))
    logger.info(
        "Captured download-install metadata for model_id=%s version_id=%s at %s",
        civitai_model_id, civitai_version_id, installed_path,
    )
```

- [ ] **Step 5: Commit**

```bash
git add civitai_manager/metadata_store.py civitai_manager/main.py
git commit -m "$(cat <<'EOF'
feat: persist background metadata-capture failures as a sidecar

_track_install_metadata/_track_download_install could previously fail
silently after InvokeAI already reported the install complete — the
model would show INSTALLED with no indication its metadata capture or
trigger-word re-apply never happened. Failures are now written to a
separate {path_hash}.error.json sidecar (never the real .json one),
read by the Installed page in the next task.
EOF
)"
```

---

### Task 4: Background-error badge on the Installed page, plus dismiss route

**Files:**
- Modify: `civitai_manager/main.py`
- Modify: `civitai_manager/templates/_installed_card.html`, `civitai_manager/templates/installed_detail.html`
- Modify: `civitai_manager/static/style.css`

**Interfaces:**
- Consumes: `metadata_store.read_background_error`/`clear_background_error` (Task 3)

- [ ] **Step 1: Enrich `GET /installed`**

Replace:
```python
    for m in models:
        path = m.get("path")
        m["metadata"] = metadata_store.read_sidecar(path) if path else None
        m["path_hash"] = metadata_store.path_hash(path) if path else None
```
With:
```python
    for m in models:
        path = m.get("path")
        m["metadata"] = metadata_store.read_sidecar(path) if path else None
        m["path_hash"] = metadata_store.path_hash(path) if path else None
        m["background_error"] = metadata_store.read_background_error(path) if path else None
```

- [ ] **Step 2: Enrich `GET /installed/{path_hash}` and add the dismiss route**

Replace:
```python
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
With:
```python
@app.get("/installed/{path_hash}", response_class=HTMLResponse)
async def installed_detail(request: Request, path_hash: str, return_to: str = ""):
    try:
        models = await request.app.state.invokeai.list_models()
    except httpx.HTTPError as exc:
        return render_error(request, summarize_upstream_error(exc, "InvokeAI"), status_code=502)
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
        "path_hash": path_hash,
        "background_error": metadata_store.read_background_error(model["path"]),
        "back_url": f"/installed?{unquote(return_to)}" if return_to else "/installed",
        "civitai_url": metadata.get("civitai_url") if metadata else None,
        "commercial_use_display": (
            format_commercial_use(metadata.get("allowCommercialUse")) if metadata else None
        ),
    }
    return templates.TemplateResponse(request, "installed_detail.html", context)


@app.post("/installed/{path_hash}/background-error/dismiss", response_class=HTMLResponse)
async def dismiss_background_error(request: Request, path_hash: str):
    try:
        models = await request.app.state.invokeai.list_models()
    except httpx.HTTPError as exc:
        return render_error(request, summarize_upstream_error(exc, "InvokeAI"), status_code=502)
    model = next(
        (m for m in models if m.get("path") and metadata_store.path_hash(m["path"]) == path_hash),
        None,
    )
    if model is None:
        return render_error(request, "That installed model could not be found.", status_code=404)
    metadata_store.clear_background_error(model["path"])
    return HTMLResponse("")
```

(`back_url` uses `return_to` here in anticipation of Task 12's filter-state preservation — with `return_to` always empty until then, `back_url` is always `/installed`, identical to today's hardcoded link.)

- [ ] **Step 3: Badge on `_installed_card.html`**

Replace:
```html
    <span class="stamp installed-card__badge">INSTALLED</span>
```
With:
```html
    <span class="stamp installed-card__badge">INSTALLED</span>
    {% if model.background_error %}
    <span class="stamp stamp--danger installed-card__badge installed-card__badge--error" title="{{ model.background_error.message }}">SYNC ISSUE</span>
    {% endif %}
```

- [ ] **Step 4: Banner + dismiss button on `installed_detail.html`**

Replace:
```html
  <div class="detail-head">
    <div class="detail-head__row">
```
With:
```html
  {% if background_error %}
  <div class="error-banner" id="background-error-banner">
    {{ background_error.message }}
    <button type="button" class="btn btn--small" hx-post="/installed/{{ path_hash }}/background-error/dismiss" hx-target="#background-error-banner" hx-swap="outerHTML">Dismiss</button>
  </div>
  {% endif %}

  <div class="detail-head">
    <div class="detail-head__row">
```

- [ ] **Step 5: CSS for the second badge**

Replace:
```css
.installed-card__badge { position: absolute; top: 0.5rem; right: 0.5rem; background: rgba(0, 0, 0, 0.55); }
```
With:
```css
.installed-card__badge { position: absolute; top: 0.5rem; right: 0.5rem; background: rgba(0, 0, 0, 0.55); }
.installed-card__badge--error { right: auto; left: 0.5rem; }
.error-banner { display: flex; align-items: center; justify-content: space-between; gap: 1rem; margin-bottom: 1.1rem; }
```

(`.error-banner`'s base rules — background/border/color/padding — already exist from the earlier `_error.html` styling; this adds the flex layout needed to sit the Dismiss button inline, since that base rule was never previously used with an inline action.)

- [ ] **Step 6: Manual verification**

Since triggering a real background-task failure requires killing CivitAI/InvokeAI at the exact right moment mid-poll, verify with a direct sidecar write instead:

```bash
cd /Users/thomasspitznas/Projects/runpod-stability-matrix && python3 -c "
import os
os.environ.setdefault('CIVITAI_METADATA_DIR', '/workspace/civitai-metadata')
from civitai_manager import metadata_store
# Use the real on-disk path of any currently-installed model (from /installed's table view's Path column):
path = 'PASTE_A_REAL_INSTALLED_MODEL_PATH_HERE'
metadata_store.write_background_error(path, 'Test: trigger words may not have saved.')
print('wrote background error for', metadata_store.path_hash(path))
"
```

1. Load `/installed` — confirm the model's card shows both `INSTALLED` (right) and `SYNC ISSUE` (left) badges, and hovering `SYNC ISSUE` shows the message as a tooltip.
2. Click into the model's detail page — confirm the red banner renders above the title with the same message and a Dismiss button.
3. Click Dismiss — confirm the banner disappears (empty response swapped in).
4. Reload `/installed` — confirm the `SYNC ISSUE` badge is now gone (the error file was cleared).

- [ ] **Step 7: Commit**

```bash
git add civitai_manager/main.py civitai_manager/templates/_installed_card.html civitai_manager/templates/installed_detail.html civitai_manager/static/style.css
git commit -m "$(cat <<'EOF'
feat: surface background metadata-capture failures on the Installed page

A SYNC ISSUE badge appears on the card and a dismissible banner on
the detail page whenever _track_install_metadata/_track_download_install
failed after the install itself succeeded.
EOF
)"
```

---

### Task 5: Toast component, wired to the dismiss action

**Files:**
- Create: `civitai_manager/templates/_toast.html`
- Modify: `civitai_manager/main.py`, `civitai_manager/templates/base.html`, `civitai_manager/static/style.css`, `civitai_manager/static/app.js`

Scoped to exactly one call site in this phase — the background-error dismiss action, whose banner just silently vanishes today with no other confirmation the click did anything. (Browse's clear-filters chip, discussed in the original design sketch, was dropped as a toast target during planning: the entire result grid visibly changing on click is already strong feedback — a toast there would be redundant.)

- [ ] **Step 1: Add the toast region to `base.html`**

Replace:
```html
  <main class="page">
    {% block content %}{% endblock %}
  </main>
</body>
```
With:
```html
  <main class="page">
    {% block content %}{% endblock %}
  </main>
  <div id="toast-region" aria-live="polite"></div>
</body>
```

- [ ] **Step 2: Write the toast macro**

Create `civitai_manager/templates/_toast.html`:
```html
{% macro toast(kind, message) %}
<div class="toast toast--{{ kind }}" hx-swap-oob="beforeend:#toast-region">{{ message }}</div>
{% endmacro %}
```

- [ ] **Step 3: Smoke-test the macro renders**

```bash
cd /Users/thomasspitznas/Projects/runpod-stability-matrix && python3 -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('civitai_manager/templates'))
module = env.get_template('_toast.html').module
html = str(module.toast('ok', 'Dismissed.'))
assert 'toast--ok' in html
assert 'Dismissed.' in html
assert 'hx-swap-oob=\"beforeend:#toast-region\"' in html
print('OK')
"
```

Expected: `OK` printed.

- [ ] **Step 4: Emit the toast from the dismiss route**

Replace:
```python
    metadata_store.clear_background_error(model["path"])
    return HTMLResponse("")
```
With:
```python
    metadata_store.clear_background_error(model["path"])
    toast_module = templates.env.get_template("_toast.html").module
    return HTMLResponse(str(toast_module.toast("ok", "Dismissed.")))
```

- [ ] **Step 5: Toast styles**

In `civitai_manager/static/style.css`, after the `.error-banner` rule added in Task 4, add:
```css
#toast-region { position: fixed; right: 1.25rem; bottom: 1.25rem; display: flex; flex-direction: column; gap: 0.5rem; z-index: 50; }
.toast {
  background: var(--surface-2); border: 1px solid var(--border-strong); border-left: 3px solid var(--ok);
  color: var(--text); padding: 0.65rem 0.9rem; border-radius: 5px; font-size: 0.85rem;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35); max-width: 320px;
}
.toast--danger { border-left-color: var(--danger); }
@media (prefers-reduced-motion: no-preference) {
  .toast { animation: toast-in 160ms ease-out; }
}
@keyframes toast-in { from { transform: translateY(6px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
```

- [ ] **Step 6: Auto-dismiss in `app.js`**

Replace:
```javascript
  function init() {
    initViewToggles();
    initInstalledTable();
  }
```
With:
```javascript
  // ---- toasts: auto-dismiss any fragment OOB-appended into #toast-region ----
  function scheduleToastDismiss(toastEl) {
    setTimeout(function () {
      toastEl.style.transition = "opacity 200ms ease";
      toastEl.style.opacity = "0";
      setTimeout(function () { toastEl.remove(); }, 200);
    }, 4000);
  }

  function initToasts() {
    var region = document.getElementById("toast-region");
    if (!region || region.dataset.observed) return;
    region.dataset.observed = "true";
    new MutationObserver(function (mutations) {
      mutations.forEach(function (m) {
        m.addedNodes.forEach(function (node) {
          if (node.nodeType === 1 && node.classList.contains("toast")) scheduleToastDismiss(node);
        });
      });
    }).observe(region, { childList: true });
  }

  function init() {
    initViewToggles();
    initInstalledTable();
    initToasts();
  }
```

- [ ] **Step 7: Manual verification**

Repeat Task 4 Step 6's dismiss flow — confirm a toast reading "Dismissed." appears bottom-right and fades out after ~4s.

- [ ] **Step 8: Commit**

```bash
git add civitai_manager/templates/_toast.html civitai_manager/templates/base.html civitai_manager/main.py civitai_manager/static/style.css civitai_manager/static/app.js
git commit -m "feat: add toast confirmation for the background-error dismiss action"
```

---

### Task 6: Loading states via htmx's built-in `.htmx-request` class

**Files:**
- Modify: `civitai_manager/static/style.css`

htmx automatically adds an `.htmx-request` class to whichever element issued a request, for the request's duration — no `hx-indicator` attribute or template change is needed for any of this app's cases: the Browse search form (`<form class="intake" hx-get=...>`), every Install/Download form (`<form hx-post=...>`), each version-tab anchor (`hx-get` on the `<a>` itself), and each gallery div (`hx-get` on itself, `hx-trigger="load"`).

- [ ] **Step 1: Add the loading-state rules**

In `civitai_manager/static/style.css`, after the `.version-tab--static` rules, add:
```css
form.htmx-request .btn { opacity: 0.6; pointer-events: none; }
.version-tab.htmx-request { opacity: 0.55; }

.gallery.htmx-request { min-height: 120px; position: relative; }
.gallery.htmx-request::before {
  content: ""; position: absolute; inset: 0; border-radius: 5px;
  background: linear-gradient(90deg, var(--surface) 25%, var(--surface-2) 37%, var(--surface) 63%);
  background-size: 400% 100%; animation: shimmer 1.4s ease infinite;
}
@keyframes shimmer { 0% { background-position: 100% 0; } 100% { background-position: 0 0; } }
@media (prefers-reduced-motion: reduce) {
  .gallery.htmx-request::before { animation: none; }
}
```

- [ ] **Step 2: Manual verification**

In browser devtools, throttle network to "Slow 3G". Confirm:
1. Submitting the Browse search dims the Search button for the request's duration.
2. Clicking a version tab dims that tab briefly.
3. Clicking Install/Download dims the button and blocks a second click (double-submit guard, incidental to this change).
4. Loading a model detail page shows a shimmering placeholder in the gallery area before images appear.

- [ ] **Step 3: Commit**

```bash
git add civitai_manager/static/style.css
git commit -m "feat: add loading-state styling via htmx's built-in .htmx-request class"
```

---

### Task 7: Clear-filters chip on Browse

**Files:**
- Modify: `civitai_manager/main.py`, `civitai_manager/templates/browse_results.html`, `civitai_manager/static/style.css`

- [ ] **Step 1: Compute `has_active_filters` in `GET /browse`**

Replace:
```python
    context = {
        "request": request,
        "active_nav": "browse",
        "models": results.get("items", results.get("models", [])),
        "metadata": results.get("metadata", {}),
        "q": q,
        "types": types,
        "base_model": base_model,
        "base_model_choices": BASE_MODEL_CHOICES,
        "sort": sort,
        "period": period,
        "nsfw": nsfw,
        "cursor": cursor,
        "has_prev": bool(prev_stack),
        "prev_cursor": "" if prev_target == "_root_" else prev_target,
        "prev_param": ",".join(prev_stack[:-1]),
        "next_prev_param": ",".join([*prev_stack, cursor or "_root_"]),
        "return_to": return_to,
    }
```
With:
```python
    context = {
        "request": request,
        "active_nav": "browse",
        "models": results.get("items", results.get("models", [])),
        "metadata": results.get("metadata", {}),
        "q": q,
        "types": types,
        "base_model": base_model,
        "base_model_choices": BASE_MODEL_CHOICES,
        "sort": sort,
        "period": period,
        "nsfw": nsfw,
        "cursor": cursor,
        "has_prev": bool(prev_stack),
        "prev_cursor": "" if prev_target == "_root_" else prev_target,
        "prev_param": ",".join(prev_stack[:-1]),
        "next_prev_param": ",".join([*prev_stack, cursor or "_root_"]),
        "return_to": return_to,
        "has_active_filters": bool(
            q or type_list or base_model or sort != "Most Downloaded" or period != "AllTime" or nsfw == "false"
        ),
    }
```

- [ ] **Step 2: Add the chip to `browse_results.html`**

Replace:
```html
  <div class="results__toolbar">
    <span class="refresh-link"
```
With:
```html
  <div class="results__toolbar">
    {% if has_active_filters %}
    <a class="chip chip--clear" href="/browse"
       hx-get="/browse" hx-target="#results" hx-swap="outerHTML" hx-push-url="true">Clear filters &times;</a>
    {% endif %}
    <span class="refresh-link"
```

- [ ] **Step 3: Style the chip**

In `civitai_manager/static/style.css`, replace:
```css
.chip { font-family: var(--font-mono); font-size: 0.7rem; color: var(--text-muted); border: 1px solid var(--border); border-radius: 3px; padding: 0.2rem 0.5rem; }
```
With:
```css
.chip { font-family: var(--font-mono); font-size: 0.7rem; color: var(--text-muted); border: 1px solid var(--border); border-radius: 3px; padding: 0.2rem 0.5rem; }
.chip--clear { cursor: pointer; color: var(--danger); border-color: var(--danger); }
.chip--clear:hover { background: rgba(250, 82, 82, 0.12); }
```

- [ ] **Step 4: Manual verification**

1. Load `/browse` with no filters — confirm no chip appears.
2. Type a search query or change a select — confirm the chip appears after results load.
3. Click it — confirm it navigates to the unfiltered first page and the chip disappears again.

- [ ] **Step 5: Commit**

```bash
git add civitai_manager/main.py civitai_manager/templates/browse_results.html civitai_manager/static/style.css
git commit -m "feat: add clear-filters chip to Browse when any filter is active"
```

---

### Task 8: Downloads detail page and thumbnails

**Files:**
- Create: `civitai_manager/templates/download_detail.html`
- Modify: `civitai_manager/main.py`, `civitai_manager/templates/_install_panel.html`, `civitai_manager/templates/downloads.html`, `civitai_manager/static/style.css`

A table with a thumbnail column and a real detail page — not a grid/card view toggle — is the right amount of investment for a short, transient, action-oriented list (see design spec's Architecture section for the reasoning). All data the detail page needs already exists in the download sidecar (`downloads.py`); only a `thumbnail_url` field is new.

- [ ] **Step 1: Accept and store `thumbnail_url` in `POST /download`**

Replace:
```python
async def download(
    request: Request,
    download_url: str = Form(...),
    filename: str = Form(...),
    sha256: str = Form(""),
    model_id: str = Form(""),
    model_name: str = Form(""),
    model_type: str = Form(""),
    version_id: str = Form(""),
    base_model: str = Form(""),
    civitai_url: str = Form(""),
    description: str = Form(""),
    trigger_words: str = Form(""),
):
```
With:
```python
async def download(
    request: Request,
    download_url: str = Form(...),
    filename: str = Form(...),
    sha256: str = Form(""),
    model_id: str = Form(""),
    model_name: str = Form(""),
    model_type: str = Form(""),
    version_id: str = Form(""),
    base_model: str = Form(""),
    civitai_url: str = Form(""),
    description: str = Form(""),
    trigger_words: str = Form(""),
    thumbnail_url: str = Form(""),
):
```

- [ ] **Step 2: Validate and persist it**

Replace:
```python
    if civitai_url and not civitai_url.startswith(("http://", "https://")):
        civitai_url = ""
    trigger_words_list = [w for w in (w.strip() for w in trigger_words.split(",")) if w]
    metadata = {
        "model_id": model_id or None,
        "model_name": model_name or None,
        "model_type": model_type or None,
        "version_id": version_id or None,
        "base_model": base_model or None,
        "civitai_url": civitai_url or None,
        "description": description or None,
        "trigger_words": trigger_words_list,
        "sha256": sha256 or None,
    }
```
With:
```python
    if civitai_url and not civitai_url.startswith(("http://", "https://")):
        civitai_url = ""
    if thumbnail_url and not thumbnail_url.startswith(("http://", "https://")):
        thumbnail_url = ""
    trigger_words_list = [w for w in (w.strip() for w in trigger_words.split(",")) if w]
    metadata = {
        "model_id": model_id or None,
        "model_name": model_name or None,
        "model_type": model_type or None,
        "version_id": version_id or None,
        "base_model": base_model or None,
        "civitai_url": civitai_url or None,
        "description": description or None,
        "trigger_words": trigger_words_list,
        "sha256": sha256 or None,
        "thumbnail_url": thumbnail_url or None,
    }
```

- [ ] **Step 3: Send `thumbnail_url` from the Download-to-folder form**

In `civitai_manager/templates/_install_panel.html`, replace:
```html
      <form hx-post="/download" hx-target="#status-messages-{{ active_version.id }}" hx-swap="innerHTML">
        <input type="hidden" name="download_url" value="{{ f.downloadUrl }}">
        <input type="hidden" name="filename" value="{{ f.name }}">
        <input type="hidden" name="sha256" value="{{ f.hashes.SHA256 or '' }}">
        <input type="hidden" name="model_id" value="{{ model.id }}">
        <input type="hidden" name="model_name" value="{{ model.name }}">
        <input type="hidden" name="model_type" value="{{ model.type }}">
        <input type="hidden" name="version_id" value="{{ active_version.id }}">
        <input type="hidden" name="base_model" value="{{ active_version.baseModel }}">
        <input type="hidden" name="civitai_url" value="{{ civitai_url }}">
        <input type="hidden" name="description" value="{{ model.description or '' }}">
        <input type="hidden" name="trigger_words" value="{{ (active_version.trainedWords or []) | join(',') }}">
        <button type="submit" class="btn">Download to folder</button>
      </form>
```
With:
```html
      <form hx-post="/download" hx-target="#status-messages-{{ active_version.id }}" hx-swap="innerHTML">
        <input type="hidden" name="download_url" value="{{ f.downloadUrl }}">
        <input type="hidden" name="filename" value="{{ f.name }}">
        <input type="hidden" name="sha256" value="{{ f.hashes.SHA256 or '' }}">
        <input type="hidden" name="model_id" value="{{ model.id }}">
        <input type="hidden" name="model_name" value="{{ model.name }}">
        <input type="hidden" name="model_type" value="{{ model.type }}">
        <input type="hidden" name="version_id" value="{{ active_version.id }}">
        <input type="hidden" name="base_model" value="{{ active_version.baseModel }}">
        <input type="hidden" name="civitai_url" value="{{ civitai_url }}">
        <input type="hidden" name="description" value="{{ model.description or '' }}">
        <input type="hidden" name="trigger_words" value="{{ (active_version.trainedWords or []) | join(',') }}">
        <input type="hidden" name="thumbnail_url" value="{{ active_version.images[0].url if active_version.images else '' }}">
        <button type="submit" class="btn">Download to folder</button>
      </form>
```

- [ ] **Step 4: Add the detail route**

Replace:
```python
@app.get("/downloads", response_class=HTMLResponse)
async def downloads_list(request: Request):
    files = downloads.list_downloaded_files(Path(config.CIVITAI_DOWNLOAD_DIR))
    try:
        installed_models = await request.app.state.invokeai.list_models()
        installed_paths = {
            str(Path(m["path"]).resolve()) for m in installed_models if m.get("path")
        }
        invokeai_error = None
    except httpx.HTTPError:
        installed_paths = set()
        invokeai_error = "InvokeAI is not reachable right now — install status is unknown."
    for f in files:
        f["installed"] = str(f["path"].resolve()) in installed_paths
    return templates.TemplateResponse(
        request,
        "downloads.html",
        {"files": files, "error": invokeai_error, "active_nav": "downloads"},
    )
```
With:
```python
@app.get("/downloads", response_class=HTMLResponse)
async def downloads_list(request: Request):
    files = downloads.list_downloaded_files(Path(config.CIVITAI_DOWNLOAD_DIR))
    try:
        installed_models = await request.app.state.invokeai.list_models()
        installed_paths = {
            str(Path(m["path"]).resolve()) for m in installed_models if m.get("path")
        }
        invokeai_error = None
    except httpx.HTTPError:
        installed_paths = set()
        invokeai_error = "InvokeAI is not reachable right now — install status is unknown."
    for f in files:
        f["installed"] = str(f["path"].resolve()) in installed_paths
    return templates.TemplateResponse(
        request,
        "downloads.html",
        {"files": files, "error": invokeai_error, "active_nav": "downloads"},
    )


@app.get("/downloads/{filename}", response_class=HTMLResponse)
async def download_detail(request: Request, filename: str):
    download_dir = Path(config.CIVITAI_DOWNLOAD_DIR).resolve()
    target = (download_dir / filename).resolve()
    if download_dir not in target.parents or not target.is_file():
        return render_error(request, "That file could not be found.", status_code=404)
    metadata = downloads.read_sidecar(target)
    try:
        installed_models = await request.app.state.invokeai.list_models()
        installed = str(target) in {
            str(Path(m["path"]).resolve()) for m in installed_models if m.get("path")
        }
    except httpx.HTTPError:
        installed = False
    context = {
        "request": request,
        "file": {"name": target.name, "size": target.stat().st_size, "installed": installed},
        "metadata": metadata,
        "active_nav": "downloads",
    }
    return templates.TemplateResponse(request, "download_detail.html", context)
```

- [ ] **Step 5: Add the thumbnail column and detail link to `downloads.html`**

Replace the full contents of `civitai_manager/templates/downloads.html`:
```html
{% extends "base.html" %}
{% block title %}Downloads — CivitAI Manager{% endblock %}
{% block content %}
<div class="shell" style="padding-top: 1.5rem;">
<h1 class="page__title">Downloaded Files</h1>
{% if error %}
  <p class="empty">{{ error }}</p>
{% endif %}
{% if files %}
<table class="installed-table">
  <thead>
    <tr>
      <th>Name</th>
      <th>Size</th>
      <th>Base model</th>
      <th>Trigger words</th>
      <th>Status</th>
    </tr>
  </thead>
  <tbody>
    {% for f in files %}
    {% set m = f.metadata or {} %}
    <tr>
      <td>
        {{ f.name }}
        {% if m.model_name %}<div class="download-row__model">{{ m.model_name }}{% if m.model_type %} &middot; {{ m.model_type }}{% endif %}</div>{% endif %}
        {% if m.civitai_url %}<a class="civitai-link" href="{{ m.civitai_url }}" target="_blank" rel="noopener">View on CivitAI</a>{% endif %}
      </td>
      <td>{{ "%.2f"|format(f.size / (1024*1024*1024)) }} GB</td>
      <td>{{ m.base_model or "—" }}</td>
      <td>
        {% if m.trigger_words %}
          {% for w in m.trigger_words %}<span class="chip">{{ w }}</span>{% endfor %}
        {% else %}—{% endif %}
      </td>
      <td>
        <div class="status-messages" id="status-messages-{{ loop.index }}">
          {% if f.installed %}
          <span class="stamp stamp--ok">INSTALLED</span>
          {% else %}
          <form hx-post="/downloads/{{ f.name | urlencode }}/install" hx-target="#status-messages-{{ loop.index }}" hx-swap="innerHTML">
            <button type="submit" class="btn btn--accent">Install</button>
          </form>
          {% endif %}
        </div>
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% else %}
<p class="empty">No downloaded files yet — use "Download to folder" on a model's page.</p>
{% endif %}
</div>
{% endblock %}
```
With:
```html
{% extends "base.html" %}
{% block title %}Downloads — CivitAI Manager{% endblock %}
{% block content %}
<div class="shell" style="padding-top: 1.5rem;">
<h1 class="page__title">Downloaded Files</h1>
{% if error %}
  <p class="empty">{{ error }}</p>
{% endif %}
{% if files %}
<table class="installed-table">
  <thead>
    <tr>
      <th></th>
      <th>Name</th>
      <th>Size</th>
      <th>Base model</th>
      <th>Trigger words</th>
      <th>Status</th>
    </tr>
  </thead>
  <tbody>
    {% for f in files %}
    {% set m = f.metadata or {} %}
    <tr>
      <td>
        {% if m.thumbnail_url %}
        <img class="results-table__thumb" src="{{ m.thumbnail_url }}" alt="" loading="lazy">
        {% else %}
        <span class="results-table__thumb results-table__thumb--empty"></span>
        {% endif %}
      </td>
      <td>
        <a class="download-row__link" href="/downloads/{{ f.name | urlencode }}">{{ f.name }}</a>
        {% if m.model_name %}<div class="download-row__model">{{ m.model_name }}{% if m.model_type %} &middot; {{ m.model_type }}{% endif %}</div>{% endif %}
        {% if m.civitai_url %}<a class="civitai-link" href="{{ m.civitai_url }}" target="_blank" rel="noopener">View on CivitAI</a>{% endif %}
      </td>
      <td>{{ "%.2f"|format(f.size / (1024*1024*1024)) }} GB</td>
      <td>{{ m.base_model or "—" }}</td>
      <td>
        {% if m.trigger_words %}
          {% for w in m.trigger_words %}<span class="chip">{{ w }}</span>{% endfor %}
        {% else %}—{% endif %}
      </td>
      <td>
        <div class="status-messages" id="status-messages-{{ loop.index }}">
          {% if f.installed %}
          <span class="stamp stamp--ok">INSTALLED</span>
          {% else %}
          <form hx-post="/downloads/{{ f.name | urlencode }}/install" hx-target="#status-messages-{{ loop.index }}" hx-swap="innerHTML">
            <button type="submit" class="btn btn--accent">Install</button>
          </form>
          {% endif %}
        </div>
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% else %}
<p class="empty">No downloaded files yet — use "Download to folder" on a model's page.</p>
{% endif %}
</div>
{% endblock %}
```

- [ ] **Step 6: Write `download_detail.html`**

Create `civitai_manager/templates/download_detail.html`:
```html
{% extends "base.html" %}
{% block title %}{{ (metadata.model_name if metadata else None) or file.name }} — CivitAI Manager{% endblock %}
{% block content %}
<div class="detail-shell">
  <a class="back-link" href="/downloads">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"></polyline></svg>
    Back to downloads
  </a>

  <div class="detail-head">
    <div class="detail-head__row">
      <h1 class="detail-head__title">{{ (metadata.model_name if metadata else None) or file.name }}</h1>
      {% if metadata and metadata.civitai_url %}
      <a class="civitai-link" href="{{ metadata.civitai_url }}" target="_blank" rel="noopener">
        View on CivitAI
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg>
      </a>
      {% endif %}
    </div>
    <div class="tag-row">
      {% if metadata and metadata.model_type %}<span class="tag tag--type">{{ metadata.model_type }}</span>{% endif %}
      {% if file.installed %}<span class="stamp stamp--ok">INSTALLED</span>{% endif %}
    </div>
  </div>

  <div class="detail-grid">
    <div class="main-pane">
      {% if metadata and metadata.description %}
      {# sanitized in CivitAIClient.get_model (bleach allowlist) before this was captured to the sidecar #}
      <div class="desc is-collapsed" id="download-desc">{{ metadata.description | safe }}</div>
      <button type="button" class="desc-toggle" onclick="var el=document.getElementById('download-desc'); var collapsed=el.classList.toggle('is-collapsed'); this.textContent = collapsed ? 'Show more' : 'Show less';">Show more</button>
      {% endif %}

      {% if metadata and metadata.trigger_words %}
      <p class="trigger-words">
        {% for w in metadata.trigger_words %}<code>{{ w }}</code>{% endfor %}
      </p>
      {% endif %}

      {% if metadata and metadata.model_id and metadata.version_id %}
      <div class="gallery" id="gallery-{{ metadata.version_id }}"
           hx-get="/models/{{ metadata.model_id }}/versions/{{ metadata.version_id }}/gallery"
           hx-trigger="load" hx-swap="innerHTML"></div>
      {% endif %}

      {% if not metadata %}
      <p class="empty">No CivitAI metadata available for this file — it may have been placed in the folder manually rather than downloaded through CivitAI Manager.</p>
      {% endif %}
    </div>

    <aside class="sidebar">
      <div class="panel">
        <div class="panel__heading">File</div>
        <div class="stat-list">
          <div class="stat-row"><span class="stat-row__label">Size</span><span class="stat-row__value">{{ "%.2f"|format(file.size / (1024*1024*1024)) }} GB</span></div>
          <div class="stat-row"><span class="stat-row__label">Base model</span><span class="stat-row__value">{{ metadata.base_model if metadata else "—" }}</span></div>
          <div class="stat-row"><span class="stat-row__label">Status</span><span class="stat-row__value">{{ "Installed" if file.installed else "Not installed" }}</span></div>
        </div>
      </div>
      {% if not file.installed %}
      <div class="panel">
        <div class="status-messages" id="status-messages-detail"></div>
        <form hx-post="/downloads/{{ file.name | urlencode }}/install" hx-target="#status-messages-detail" hx-swap="innerHTML">
          <button type="submit" class="btn btn--accent" style="width:100%;">Install</button>
        </form>
      </div>
      {% endif %}
    </aside>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 7: Style the new table cells**

In `civitai_manager/static/style.css`, after `.results-table__downloads`, add:
```css
.results-table__thumb--empty { display: inline-block; border: 1px solid var(--border); }
.download-row__link { font-weight: 500; }
.download-row__model { font-size: 0.78rem; color: var(--text-dim); margin-top: 0.15rem; }
```

- [ ] **Step 8: Manual verification**

1. Download a file via a model's detail page. Confirm the Downloads table now shows a thumbnail.
2. Click the filename — confirm the detail page renders: title, tag, gallery (if the model has example images), description, trigger words, and an Install button (since it isn't installed yet).
3. Click Install from the detail page — confirm it installs correctly (same route the Downloads table already used).
4. Manually place a file with no sidecar in `CIVITAI_DOWNLOAD_DIR` — confirm its row shows an empty thumbnail placeholder and its detail page shows the "No CivitAI metadata available" message without erroring.
5. Confirm `/downloads/../../etc/passwd`-style paths still 404 (path-traversal guard, same pattern as the existing install route).

- [ ] **Step 9: Commit**

```bash
git add civitai_manager/main.py civitai_manager/templates/_install_panel.html civitai_manager/templates/downloads.html civitai_manager/templates/download_detail.html civitai_manager/static/style.css
git commit -m "$(cat <<'EOF'
feat: add Downloads detail page with thumbnails

Closes the workflow gap where Downloads was the only one of the
three main pages with no detail view or thumbnails. Reuses the
existing download sidecar and gallery route — only a new
thumbnail_url sidecar field was needed.
EOF
)"
```

---

### Task 9: Static version-tab visual cue

**Files:**
- Modify: `civitai_manager/static/style.css`, `civitai_manager/templates/installed_detail.html`

`installed_detail.html`'s non-interactive version tabs already have `cursor: default` — but look otherwise identical to Browse's real, clickable tabs at rest, which is easy to miss since most users don't hover to check the cursor before clicking.

- [ ] **Step 1: Sharpen the resting-state style**

Replace:
```css
.version-tab--static { cursor: default; }
.version-tab--static:hover { border-color: var(--border); color: var(--text-muted); }
.version-tab--static.is-active:hover { border-color: transparent; color: #fff; }
```
With:
```css
.version-tab--static { cursor: default; opacity: 0.7; }
.version-tab--static:hover { border-color: var(--border); color: var(--text-muted); opacity: 0.7; }
.version-tab--static.is-active,
.version-tab--static.is-active:hover { opacity: 1; border-color: transparent; color: #fff; }
```

- [ ] **Step 2: Add a tooltip**

Replace:
```html
        <span class="version-tab version-tab--static{{ ' is-active' if v.id == metadata.installed_version_id else '' }}">{{ v.name }}{{ ' (installed)' if v.id == metadata.installed_version_id else '' }}</span>
```
With:
```html
        <span class="version-tab version-tab--static{{ ' is-active' if v.id == metadata.installed_version_id else '' }}" title="Only the installed version has details captured">{{ v.name }}{{ ' (installed)' if v.id == metadata.installed_version_id else '' }}</span>
```

- [ ] **Step 3: Manual verification**

Load a multi-version model's Installed detail page. Confirm inactive version tabs read visibly dimmed compared to Browse's real tabs, the active/installed one stays fully opaque, and hovering any inactive tab shows the tooltip.

- [ ] **Step 4: Commit**

```bash
git add civitai_manager/static/style.css civitai_manager/templates/installed_detail.html
git commit -m "fix: make Installed detail's non-interactive version tabs visibly inert"
```

---

### Task 10: Slow-job hint on install/download status polling

**Files:**
- Modify: `civitai_manager/static/app.js`

Fully client-side — no server timestamp threading needed, since the status fragment's element id (`install-job-{id}` / `download-job-{gid}`) stays the same string across every poll swap, so a JS-side map keyed by that id survives the swaps naturally.

- [ ] **Step 1: Add the hint logic**

Replace:
```javascript
  function init() {
    initViewToggles();
    initInstalledTable();
    initToasts();
  }
```
With:
```javascript
  // ---- slow-job hint: nudge if an install/download status fragment has
  // been polling for a while. Tracked client-side, keyed by the fragment's
  // element id, which is stable across every outerHTML poll swap. ----
  var jobStartTimes = {};
  var SLOW_JOB_MS = 5 * 60 * 1000;

  function tickSlowJobHints() {
    document.querySelectorAll(".install-status[id]").forEach(function (el) {
      if (!el.hasAttribute("hx-get")) {
        delete jobStartTimes[el.id];
        return;
      }
      if (!(el.id in jobStartTimes)) jobStartTimes[el.id] = Date.now();
      var elapsed = Date.now() - jobStartTimes[el.id];
      if (elapsed > SLOW_JOB_MS && !el.querySelector(".install-status__slow-hint")) {
        var hint = document.createElement("span");
        hint.className = "install-status__slow-hint";
        hint.textContent = "still going — this is taking longer than usual";
        el.appendChild(hint);
      }
    });
  }

  setInterval(tickSlowJobHints, 15000);

  function init() {
    initViewToggles();
    initInstalledTable();
    initToasts();
  }
```

- [ ] **Step 2: Manual verification**

Temporarily change `SLOW_JOB_MS = 5 * 60 * 1000` to `SLOW_JOB_MS = 5000` for testing. Start an install or download, wait ~20s (past two 15s ticks), confirm the "still going" hint appears next to the status line. Revert the constant back to `5 * 60 * 1000` before committing.

- [ ] **Step 3: Commit**

```bash
git add civitai_manager/static/app.js
git commit -m "feat: show a soft hint when an install/download poll runs long"
```

---

### Task 11: Debounce the Installed page filter input

**Files:**
- Modify: `civitai_manager/static/app.js`

- [ ] **Step 1: Debounce the input handler**

Replace:
```javascript
    filterInput.addEventListener("input", render);
    typeSelect.addEventListener("change", render);
```
With:
```javascript
    var filterDebounce;
    filterInput.addEventListener("input", function () {
      clearTimeout(filterDebounce);
      filterDebounce = setTimeout(render, 150);
    });
    typeSelect.addEventListener("change", render);
```

- [ ] **Step 2: Manual verification**

On `/installed` with several models, type quickly into the filter box. Confirm the list still narrows correctly but doesn't visibly re-render on every keystroke (no jank on a fast type).

- [ ] **Step 3: Commit**

```bash
git add civitai_manager/static/app.js
git commit -m "perf: debounce the Installed page filter input"
```

---

### Task 12: Installed page filter/sort state preservation across navigation (optional — cut first if this phase needs to shrink)

**Files:**
- Modify: `civitai_manager/static/app.js`, `civitai_manager/templates/_installed_card.html`, `civitai_manager/templates/installed.html`

This is the highest-complexity item in the plan. Mirrors Browse's `return_to` pattern, adapted for Installed's client-side (not server-round-trip) filtering: the current filter/type/sort state is kept in the URL via `history.replaceState` (not `pushState`, to avoid trashing back-button history on every keystroke), and card/row links carry it forward as `?return_to=...` so the detail page's "Back to installed" link can restore it. The browser's native Back button is not wired to step through filter changes — only the in-app "Back to installed" link is; this is an accepted limitation, not a bug (see Step 6).

- [ ] **Step 1: Add `data-path-hash` so JS can rebuild each link's href**

In `civitai_manager/templates/_installed_card.html`, replace:
```html
<a class="card installed-card" href="/installed/{{ model.path_hash }}"
   data-installed-row
   data-name="{{ model.get('name', '—') }}"
   data-type="{{ model.get('type', '—') }}"
   data-base="{{ model.get('base', '—') }}"
   data-path="{{ model.get('path', '—') }}">
```
With:
```html
<a class="card installed-card" href="/installed/{{ model.path_hash }}"
   data-installed-row
   data-path-hash="{{ model.path_hash }}"
   data-name="{{ model.get('name', '—') }}"
   data-type="{{ model.get('type', '—') }}"
   data-base="{{ model.get('base', '—') }}"
   data-path="{{ model.get('path', '—') }}">
```

In `civitai_manager/templates/installed.html`, replace:
```html
      <tr data-installed-row data-row-href="/installed/{{ model.path_hash }}" style="cursor:pointer;"
          data-name="{{ model.get('name', '—') }}"
          data-type="{{ model.get('type', '—') }}"
          data-base="{{ model.get('base', '—') }}"
          data-path="{{ model.get('path', '—') }}">
```
With:
```html
      <tr data-installed-row data-row-href="/installed/{{ model.path_hash }}" data-path-hash="{{ model.path_hash }}" style="cursor:pointer;"
          data-name="{{ model.get('name', '—') }}"
          data-type="{{ model.get('type', '—') }}"
          data-base="{{ model.get('base', '—') }}"
          data-path="{{ model.get('path', '—') }}">
```

- [ ] **Step 2: Restore state on load and sync it back into the URL on every render**

Replace:
```javascript
    var total = rows.length;
    var sort = { key: "name", dir: 1 };

    function render() {
```
With:
```javascript
    var total = rows.length;
    var sort = { key: "name", dir: 1 };

    (function restoreFromUrl() {
      var params = new URLSearchParams(location.search);
      if (params.has("filter")) filterInput.value = params.get("filter");
      if (params.has("type")) typeSelect.value = params.get("type");
      if (params.has("sort")) sort.key = params.get("sort");
      if (params.has("dir")) sort.dir = parseInt(params.get("dir"), 10) || 1;
    })();

    function currentQuery() {
      var params = new URLSearchParams();
      if (filterInput.value) params.set("filter", filterInput.value);
      if (typeSelect.value) params.set("type", typeSelect.value);
      params.set("sort", sort.key);
      params.set("dir", sort.dir);
      return params.toString();
    }

    function render() {
```

- [ ] **Step 3: Update history and each link's href on every render**

Replace:
```javascript
      countEl.textContent = visible.length + " of " + total + " installed";

      sortButtons.forEach(function (btn) {
        var isActive = btn.dataset.sortKey === sort.key;
        btn.classList.toggle("is-active", isActive);
        var svg = btn.querySelector("svg");
        if (svg) svg.classList.toggle("is-desc", isActive && sort.dir === -1);
      });
    }
```
With:
```javascript
      countEl.textContent = visible.length + " of " + total + " installed";

      sortButtons.forEach(function (btn) {
        var isActive = btn.dataset.sortKey === sort.key;
        btn.classList.toggle("is-active", isActive);
        var svg = btn.querySelector("svg");
        if (svg) svg.classList.toggle("is-desc", isActive && sort.dir === -1);
      });

      var query = currentQuery();
      history.replaceState(null, "", query ? "?" + query : location.pathname);
      var returnTo = "?return_to=" + encodeURIComponent(query);
      rows.forEach(function (r) {
        r.card.href = "/installed/" + r.card.dataset.pathHash + returnTo;
        r.row.dataset.rowHref = "/installed/" + r.row.dataset.pathHash + returnTo;
      });
    }
```

- [ ] **Step 4: Apply the restored state to the sort-button visuals before the first render**

Replace:
```javascript
    filterInput.addEventListener("input", function () {
      clearTimeout(filterDebounce);
      filterDebounce = setTimeout(render, 150);
    });
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
```
With:
```javascript
    filterInput.addEventListener("input", function () {
      clearTimeout(filterDebounce);
      filterDebounce = setTimeout(render, 150);
    });
    typeSelect.addEventListener("change", render);
    sortButtons.forEach(function (btn) {
      btn.addEventListener("click", function () {
        var key = btn.dataset.sortKey;
        if (sort.key === key) sort.dir *= -1;
        else { sort.key = key; sort.dir = 1; }
        render();
      });
    });

    render();  // also applies any filter/type/sort restored from the URL above
```

- [ ] **Step 5: Honor `return_to` on the "Back to installed" link**

`installed_detail.html`'s `back_url` context value already handles this from Task 4, Step 2 (`f"/installed?{unquote(return_to)}" if return_to else "/installed"`). Confirm its back-link markup reads from that variable:

Replace:
```html
  <a class="back-link" href="/installed">
```
With:
```html
  <a class="back-link" href="{{ back_url }}">
```

- [ ] **Step 6: Manual verification**

1. On `/installed`, type a filter and pick a type — confirm the URL updates (e.g. `?filter=lora&type=LORA&sort=name&dir=1`) without adding new browser-history entries per keystroke (press Back once from here and confirm it leaves `/installed` entirely, landing on whatever page was visited before, not an earlier filter state).
2. Click a card while filters are active — confirm you land on the detail page, then click "Back to installed" — confirm the filter/type/sort state and results are restored exactly as left.
3. Switch to table view, sort by a column, click a row, click Back — confirm the same restoration works for the table.
4. Confirm the raw browser Back button from the detail page does **not** restore filters (it returns to the last real navigation entry) — this is the documented, accepted gap; only the explicit "Back to installed" link restores state.

- [ ] **Step 7: Commit**

```bash
git add civitai_manager/static/app.js civitai_manager/templates/_installed_card.html civitai_manager/templates/installed.html civitai_manager/templates/installed_detail.html
git commit -m "$(cat <<'EOF'
feat: preserve Installed page filter/sort state across detail navigation

Mirrors Browse's return_to pattern, adapted for Installed's
client-side filtering: state lives in the URL via replaceState (not
pushState, to avoid trashing back-button history per keystroke), and
card/row links carry it forward so the detail page's Back link can
restore it exactly.
EOF
)"
```

---

### Task 13: Final verification and documentation

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Full manual walkthrough**

Against a running pod (InvokeAI + aria2-rpc + civitai-manager all up):
1. Search → view a model's detail page → Download to folder → confirm thumbnail appears in Downloads → click into its detail page → Install from there → confirm it completes and appears on `/installed`.
2. Search → view a model's detail page → Install directly → confirm the sidecar is captured and the model's Installed card/detail show full CivitAI metadata.
3. Repeat error-path checks from Tasks 1–2 (stop InvokeAI/aria2 at each step) to confirm nothing regressed after later tasks touched the same files.
4. Apply Browse filters, confirm the clear-filters chip works; apply Installed filter/sort, navigate to a detail page and back, confirm state restoration (Task 12).
5. Confirm a background-error badge dismiss produces a toast (Task 4/5).
6. Repeat with `SERVER_ADMIN`-style auth unset and then with `CIVITAI_MANAGER_USERNAME`/`PASSWORD` set, confirming the login gate still works around every touched route.

- [ ] **Step 2: Update `CLAUDE.md`**

In `/Users/thomasspitznas/Projects/runpod-stability-matrix/CLAUDE.md`, in the "CivitAI Manager (port 8000)" section, after the paragraph ending "Models without a sidecar (installed outside the app, or before this feature existed) still render, just without CivitAI metadata.", add:

```markdown

**Error surfacing & background-error visibility**: `civitai_manager/errors.py`'s `summarize_upstream_error()` classifies `httpx` exceptions (connect failure, timeout, HTTP status with parsed JSON detail) into distinct user-facing messages, used at every upstream call site instead of one generic string. The install/download status polling fragments (`_install_status.html`/`_download_status.html`) retry a bounded number of times on a transient error before falling back to a manual Retry button, and show a soft "taking longer than usual" hint (tracked client-side in `app.js`, keyed by the fragment's stable element id) past 5 minutes. Background metadata-capture failures (`_track_install_metadata`/`_track_download_install` in `main.py`) are persisted as a second sidecar (`metadata_store.write_background_error`, `{path_hash}.error.json` — separate from the main `.json` metadata sidecar so a background failure can never corrupt it) and surfaced as a "SYNC ISSUE" badge on the Installed card/detail page, dismissible via `POST /installed/{path_hash}/background-error/dismiss`. A small OOB-swap toast component (`_toast.html`'s `toast()` macro, appended into `base.html`'s `#toast-region`) confirms the dismiss action; it isn't used app-wide since most other actions already have inline feedback.

**Downloads detail page**: `GET /downloads/{filename}` (`download_detail.html`) gives a downloaded-but-not-yet-installed file the same thumbnail-plus-detail-page treatment as Browse/Installed, sourced from the same download sidecar (`downloads.py`) — including a thumbnail (`thumbnail_url`, captured from the model's first version image when the download form is submitted) and the shared `/models/{id}/versions/{id}/gallery` route. Deliberately no grid/card view toggle on the Downloads list itself — a table with a thumbnail column was judged the right amount of investment for a short, transient, action-oriented list, unlike Browse/Installed's genuine browsing/audit role.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document Phase 1 UX foundation additions to CivitAI Manager"
```

---

## Self-Review Notes

- **Spec coverage:** Error-surfacing infrastructure (Tasks 1–2), background-task failure visibility (Tasks 3–4), toast component (Task 5), loading states (Task 6), Downloads detail page (Task 8), static-tab cue (Task 9), polling timeout hint (Task 10), filter debounce (Task 11), filter-state preservation (Task 12) — every item in the design spec's Component/Route Changes table has a task. The one deliberate deviation from the original design sketch — dropping the toast's second call site (Browse's clear-filters chip) — is called out explicitly in Task 5 with its reasoning, not silently dropped.
- **Placeholder scan:** No task defers detail to "add appropriate error handling" or similar — every step shows the literal code, and every route/template change is a full or exact-snippet diff against the real current file contents (verified by reading each file before drafting its diff).
- **Type/name consistency check:** `summarize_upstream_error` (Task 1) is imported and called with the exact same signature in Tasks 1, 2, and 4. `write_background_error`/`read_background_error`/`clear_background_error` (Task 3) are the exact names used in Task 4's route and template context (`background_error`, `model.background_error`). `path_hash` context key added in Task 4 is the same name Task 12 relies on for `data-path-hash`. `_toast.html`'s `toast(kind, message)` macro signature matches its one call site in Task 5. `back_url`/`return_to` context keys introduced in Task 4 are the exact ones Task 12 wires up on the client side and Task 9 leaves untouched.
