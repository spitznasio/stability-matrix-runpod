# CivitAI Manager Phase 1 UX Foundation — Design Spec

## Problem

CivitAI Manager (`civitai_manager/`, port 8000) has good architectural bones — htmx partial swaps, a real design-token system, a working background-metadata-capture pipeline (`metadata_store.py`) — but several concrete gaps undercut the goal of a trustworthy, polished tool:

- ~10 call sites collapse every upstream failure mode behind `except httpx.HTTPError` into one generic message (e.g. "InvokeAI is not ready yet, or the install request was rejected — try again shortly." for both "InvokeAI is down" and "you sent a malformed URL"). One site (`GET /models/{id}/versions/{id}/gallery`) fails with **zero logging**.
- The background tasks that capture CivitAI metadata after an install (`_track_install_metadata`, `_track_download_install`) can fail — CivitAI/InvokeAI unreachable mid-poll — with **no trace anywhere in the UI**. An install can show "INSTALLED" while its metadata capture silently never happens, permanently.
- Zero `hx-indicator` usage anywhere (confirmed by grep) — no loading affordance on any htmx swap.
- Downloads has no detail page or thumbnails, unlike Browse and Installed — the search→download→install pattern breaks down on that page.
- Installed's non-interactive "static" version tabs (`installed_detail.html`) look almost identical to real clickable tabs at rest.
- The foreground install/download polling fragments (`_install_status.html`/`_download_status.html`) poll forever with no "this is taking a while" signal, and stop retrying entirely (not just informing) on a transient error.
- Browse's 5-select filter row has no clear/reset affordance.
- Installed's client-side filter input has no debounce, and its filter/sort state isn't preserved when navigating to a detail page and back (unlike Browse's `return_to` mechanism).

Goal: fix all of the above while reusing the existing dark theme, IBM Plex typography, and component vocabulary (`.stamp`, `.btn`, `.chip`, `.card`) — elevate, don't replace.

## Non-goals

- No new visual identity, no light theme (per user decision — see approved plan at conversation time).
- No CivitAI API expansion (richer stats/license badges) — deferred to a Phase 2 spec.
- No InvokeAI direct-lookup-by-key improvement to `GET /installed/{path_hash}` — deferred to Phase 2.
- No test framework introduced — this repo has none (`civitai_manager/` has zero `test_*.py` files), consistent with prior plans (`docs/superpowers/plans/2026-07-19-installed-page-mirror.md`). Verification stays `python3 -c` smoke checks for pure functions plus manual curl/browser checks for routes and templates.
- No change to `/browse`, `/models/{id}`'s core search/detail behavior beyond the error-message and loading-indicator work described here.

## Architecture

### Error-surfacing infrastructure

A new pure function, `civitai_manager/errors.py`'s `summarize_upstream_error(exc, service)`, replaces every ad hoc "collapse to one generic string" except-block. It classifies `httpx` exceptions into distinct, still non-technical messages:

- `HTTPStatusError` → `"{service} rejected the request (HTTP {status})"`, appending a parsed `detail`/`error`/`message` JSON field when the response body has one (generalizes the CivitAI-503 parsing already ad hoc in `main.py`'s global handler).
- `ConnectError`/`ConnectTimeout` → `"Could not reach {service} — check that it's running."`
- `TimeoutException` → `"{service} timed out responding."`
- Anything else → `f"{service} request failed: {exc}"` (already produces a crafted, useful string for aria2's RPC-error case, since `aria2_client._call` bakes `body["error"]` into the exception message).

This function is reused at every existing except-block (`/install`, `/downloads/{filename}/install`, `/download`, the global `httpx.HTTPError` handler) so each surfaces a distinct message instead of an identical one.

The two polling-status routes (`/install/{job_id}/status`, `/download/{gid}/status`) get more than a message swap: today, on `httpx.HTTPError`, the returned fragment simply omits its `hx-get`/`hx-trigger` attributes, permanently freezing the poll. They're changed to retry a bounded number of times (carrying an `attempt` count through each response) before falling back to a fragment with no auto-poll and a manual "Retry" button.

### Background-task failure visibility

`_track_install_metadata`/`_track_download_install` can fail after InvokeAI has already reported the install itself complete (CivitAI unreachable when re-fetching model data; InvokeAI unreachable when re-applying `trigger_phrases`/`source_url`). These failures are invisible today. Three new functions in `metadata_store.py`, sibling to `write_sidecar`/`read_sidecar` and using the same `path_hash`-keyed scheme but a **separate** file suffix (`.error.json`, never `.json`) so a background failure can never corrupt or block the real metadata sidecar:

```python
def write_background_error(model_path: str, message: str) -> None
def read_background_error(model_path: str) -> dict | None   # {"message": str, "occurred_at": iso8601}
def clear_background_error(model_path: str) -> None
```

Wired into both background tasks' except-blocks (only the "install already succeeded but the follow-up failed" cases — not the "install job itself failed," which is already visible via the terminal `stamp--danger` in `_install_status.html`). Surfaced as a second `.stamp.stamp--danger` badge on `_installed_card.html`/`installed_detail.html`, with a `POST /installed/{path_hash}/background-error/dismiss` route that clears it and fires a toast.

### Toast component

Server-rendered, htmx-only — no client-side state beyond auto-dismiss timers. `base.html` gains a `#toast-region` div; a `_toast.html` fragment is appended into it via the same out-of-band swap pattern `_version_update.html` already uses for the sidebar install panel (`hx-swap-oob="beforeend:#toast-region"`). `app.js` gains a small listener that fades and removes each toast ~4s after it lands. Scoped to exactly two call sites in this phase — the background-error dismiss action and Browse's new clear-filters chip — not applied blanket-wide, since most of the app's feedback is already inline (install/download status stamps).

### Downloads detail page

Rather than adding a grid/card view toggle to Downloads (a short, transient, action-oriented list, unlike Browse/Installed's genuine browsing/audit role), Downloads gets a real per-file detail page and a thumbnail column in its existing table — closing the actual functional gap (no way to review what you downloaded, no visual identity for a downloaded-but-not-installed file). All data the detail page needs is already captured in the download sidecar (`downloads.py`); a `thumbnail_url` field is added to that sidecar, threaded from the model's first version image via a new hidden form field on the two "Download to folder" forms.

### Everything else

Loading states (`hx-indicator`/`.htmx-request` CSS, applied to search/version-tabs/gallery/install-download buttons), the version-tab resting-state visual cue (sharpen the existing `.version-tab--static` rule — it already has `cursor: default` but looks identical to a real tab otherwise), a soft "still going" hint past 5 minutes on the polling fragments, an input debounce and URL-synced filter/sort state for the Installed page (mirroring Browse's `return_to`) — all additive changes to existing files, reusing existing tokens/components.

## Data Model

**New file per installed model** (only written on background-task failure): `{CIVITAI_METADATA_DIR}/{path_hash}.error.json`

```json
{"message": "Trigger words/source link may not have saved to InvokeAI.", "occurred_at": "2026-07-22T10:00:00+00:00"}
```

**Extended download sidecar field** (`downloads.py`'s existing `<filename>.civitai.json`, additive key, no schema version bump needed since all consumers already treat sidecar fields as optional):

```json
{"thumbnail_url": "https://image.civitai.com/.../width=450/abc.jpeg"}
```

## Component / Route Changes

| File | Change |
|---|---|
| `civitai_manager/errors.py` (new) | `summarize_upstream_error(exc, service) -> str` |
| `civitai_manager/main.py` | Wire `summarize_upstream_error` into `/install`, `/downloads/{filename}/install`, `/download`, global handler; fix gallery route's silent catch; bounded-retry `/install/{job_id}/status`, `/download/{gid}/status`; wire background-error writes into `_track_install_metadata`/`_track_download_install`; enrich `/installed`/`/installed/{path_hash}` with `background_error`; new `POST /installed/{path_hash}/background-error/dismiss`; `has_active_filters` in `/browse`; `thumbnail_url` accepted in `/download`; new `GET /downloads/{filename}` route |
| `civitai_manager/metadata_store.py` | `write_background_error`/`read_background_error`/`clear_background_error` |
| `civitai_manager/templates/_gallery.html` | Error/retry state when the fetch fails |
| `civitai_manager/templates/_install_status.html`, `_download_status.html` | Bounded-retry + manual-retry fallback + slow-job hint |
| `civitai_manager/templates/_installed_card.html`, `installed_detail.html` | Background-error badge + dismiss button |
| `civitai_manager/templates/base.html` | `#toast-region` |
| `civitai_manager/templates/_toast.html` (new) | Toast fragment |
| `civitai_manager/templates/browse.html`, `browse_results.html` | Clear-filters chip |
| `civitai_manager/templates/_install_panel.html`, `_version_body.html`, `downloads.html` | `hx-indicator` wiring, `thumbnail_url` hidden field |
| `civitai_manager/templates/downloads.html`, `download_detail.html` (new) | Thumbnail column, detail page |
| `civitai_manager/static/style.css` | `.htmx-indicator`/`.htmx-request` rules, `.toast*`, `.chip--clear`, sharpened `.version-tab--static`, `.results-table__thumb`-reuse in Downloads |
| `civitai_manager/static/app.js` | Toast auto-dismiss, Installed filter debounce + URL state sync |

## Error Handling & Edge Cases

- **Malformed/unreadable background-error file**: `read_background_error` catches `(OSError, json.JSONDecodeError)` and returns `None` (identical pattern to `read_sidecar`) — a corrupted error file degrades to "no error shown," never crashes a page.
- **Background error for a model later uninstalled**: file becomes orphaned, harmless — same accepted technical debt as the main metadata sidecar's own orphan case (documented in `2026-07-19-installed-page-mirror-design.md`), no cleanup job in this phase.
- **Retry-capable polling exhausts its bound**: falls back to a static fragment with a manual "Retry" button that re-issues the same `GET .../status` call — never leaves the user with no path forward.
- **`_extract_installed_path` returns `None`** (job completed but no path found — the one case with no model to key a background error against): stays exactly as it behaves today (a log warning, no sidecar, no error file) — out of scope for this phase, unchanged.
- **Clear-filters chip on a request with no active filters**: simply doesn't render (`has_active_filters` is false) — no dead control shown.
- **Downloads detail page for a file with no sidecar** (pre-existing/manually placed file): renders local-only info (name, size, path) with the same "no CivitAI metadata" notice pattern `installed_detail.html` already uses for the analogous case.

## Testing Notes

No automated test suite exists in this repo. Every task in the implementation plan is verified via `python3 -c` smoke checks for pure functions (`summarize_upstream_error`, the three new `metadata_store` functions) and manual curl/browser checks for routes and templates, matching this project's established pattern. A full end-to-end walkthrough (search → download → install, error-path testing by stopping InvokeAI/aria2, filter/sort state checks) closes out the plan.
