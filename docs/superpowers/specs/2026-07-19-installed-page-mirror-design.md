# Installed Page Mirror — Design Spec

## Problem

The "Installed" page (`/installed`) currently shows models pulled from InvokeAI's
`/api/v2/models/` endpoint as a bare table: name, type, base model, path. It has none
of the affordances the "Browse" page (`/browse`) offers — no clickable cards, no
detail view, no rich CivitAI metadata (description, images, creator, stats, trigger
words, license terms).

Goal: make `/installed` functionally equivalent to `/browse` — clickable model cards
that open a rich detail page — while still being visually distinguishable as
"already installed" rather than search results.

## Non-goals

- Does not change how `/browse` or `/models/{id}` work.
- Does not attempt CivitAI lookup for models that weren't installed through this
  app's `/install` flow (see Fallback behavior below) — no name-based search
  heuristics.
- Does not add a background job to prune orphaned metadata sidecars (documented as
  acceptable technical debt, see Error Handling).

## Architecture / Data Flow

1. User clicks "Install" on a model from `/browse` or its detail page → existing
   `POST /install` handler fires.
2. `POST /install` is extended to capture the full CivitAI model payload (already
   available from the `/browse` search/detail flow — either passed through as a
   hidden form field or re-fetched via `CivitAIClient.get_model`) and, once the
   install job resolves, write it as a JSON sidecar to `/workspace/civitai-metadata/`,
   keyed by a hash of the eventual install path.
   - Since the model's on-disk path isn't known until InvokeAI's install job
     completes, and completion can only be observed by polling
     `GET /api/v2/models/install/{job_id}`, this polling **must happen server-side,
     decoupled from the client** — see "Server-side install-completion tracking"
     below for why.
3. `GET /installed` calls `InvokeAIClient.list_models()` as today, then for each
   model hashes its `path` and looks up a matching sidecar in
   `/workspace/civitai-metadata/`. Models with a sidecar get merged CivitAI data;
   models without one render as local-only.
4. Cards render via a new `_installed_card.html` partial (a variant of
   `_model_card.html`) showing the "Installed" badge, installed version label, and
   distinguishing style.
5. Clicking a card navigates to `GET /installed/{model_path_hash}` — a new route
   that loads the InvokeAI model entry (path/size/type) plus its metadata sidecar
   (if any) and renders `installed_detail.html`, structurally mirroring
   `model_detail.html`: main pane on the left (description, version/gallery info),
   sidebar on the right (creator, stats, license, "View on CivitAI" link). Local
   install context (file path, disk size, install date) is shown above the
   sidebar's CivitAI panel.

### Server-side install-completion tracking (why, and how)

The existing `_install_status.html`/`_download_status.html` pattern is client-driven:
the POST handler returns immediately with a `pending`/`in_progress` job, and an htmx
element polls a `GET .../status` endpoint every 2s **only while that element is on
the page and the tab is visible**. This is already known to be lossy — the recent
aria2 fix (`cleanup_control_file`) only runs from inside `/download` and
`/download/{gid}/status`, both client-triggered, so a control file is left behind if
the user navigates away before the poll observes a terminal status. That's tolerated
today because it's harmless: the download itself completes via aria2's own daemon
regardless of client presence, and only a leftover file lingers.

Tying the metadata sidecar write to the same client-driven pattern would be a much
worse failure mode: the InvokeAI install itself is also entirely server-side and
unaffected by the client navigating away, but if the sidecar write lived inside
`GET /install/{job_id}/status`, then any install where the user tabs away before the
final poll — plausible for large checkpoints that take minutes — would silently
install the model with **no** metadata ever captured, permanently. The model would
render as local-only on `/installed` forever, defeating the point of this feature for
what's likely the common case.

**Fix:** `POST /install` spawns a fire-and-forget `asyncio.create_task(...)` right
after starting the job, which polls `InvokeAIClient.get_install_job(job_id)`
server-side (same 2s-ish interval as the client poll, no client involvement) until it
reaches a terminal status, then writes the sidecar on success. This task's lifetime is
the FastAPI process, not the request/response cycle or any particular browser tab —
it keeps running whether or not anyone is watching. The existing client-facing
`_install_status.html` polling loop is unchanged and continues to drive the visible
"INSTALLED"/"FAILED" stamp independently; it's a UI concern only and no longer the
thing metadata capture depends on.

Failure/shutdown edge case: if the app process restarts mid-install (e.g. a
Server Admin-triggered restart of `civitai-manager`), the in-memory background task is
lost and that one install's metadata won't be captured — acceptable, same class of
gap as the path-changed case below, and rare in practice.

## Data Model

**Storage location:** `/workspace/civitai-metadata/` (persists across pod restarts,
consistent with `/workspace/invokeai` and `/workspace/civitai-downloads`).

**Filename:** `sha256(model.path).json` — hashing the InvokeAI-reported install path
avoids collisions and gives O(1) lookup from a `list_models()` entry without needing
a separate index file.

**Sidecar JSON shape** (captures everything already available from the CivitAI
model/version payload used to build `model_detail.html` — as much as is available,
degrading gracefully for fields CivitAI doesn't provide):

```json
{
  "civitai_model_id": 12345,
  "civitai_version_id": 999,
  "civitai_url": "https://civitai.com/models/12345",
  "model_name": "Model Name",
  "type": "Checkpoint",
  "base_model": "SDXL 1.0",
  "creator_username": "creator",
  "description": "<sanitized html, same pipeline as model_detail.html>",
  "trigger_words": ["trigger1", "trigger2"],
  "tags": ["tag1", "tag2"],
  "stats": {
    "downloadCount": 50000,
    "ratingCount": 123,
    "rating": 4.8
  },
  "allowCommercialUse": "Allowance",
  "allowDerivatives": true,
  "nsfw": false,
  "publishedAt": "2025-01-01T00:00:00Z",
  "versions": [
    {
      "id": 999,
      "name": "v1.0",
      "images": [{"url": "https://..."}]
    }
  ],
  "installed_version_id": 999,
  "captured_at": "2026-07-19T12:00:00Z"
}
```

`installed_version_id` records which version was actually installed (distinct from
`versions`, which is the full list available on CivitAI at capture time) — this is
what drives the "vX.Y installed" badge and lets the detail page indicate if a newer
version now exists on CivitAI.

Sanitization: `description` reuses the existing bleach-based sanitization pipeline
already applied in `CivitAIClient.get_model` — no new sanitization logic needed
since the same payload/path is reused.

## Component / Page Design

**`/installed` (list page):**
- Same shell/layout as `/browse`: toolbar (search filter, type filter, grid/table
  toggle) + grid of cards, reusing existing `installed-toolbar` JS filtering.
- Grid renders `_installed_card.html` for each model instead of the current
  `data-installed-row` + client-side-only table rendering.
- Table view keeps existing plain-row rendering (name/type/base/path), unaffected.

**`_installed_card.html`** (new partial, modeled on `_model_card.html`):
- Thumbnail (from sidecar's first version image if present, else empty-state
  placeholder — same as `_model_card.html`'s empty thumb).
- Model name, type, creator (from sidecar if present, else local InvokeAI fields
  only — name falls back to filename, creator/type shown as "—" if unknown).
- **"Installed" badge** — small accent-colored badge, top-right of thumbnail.
- **Installed version label** — e.g. "v1.0 installed", shown under the creator line
  (only rendered when sidecar data exists).
- **Distinguishing style** — subtle border/background treatment (e.g. accent-tinted
  border) so installed cards read differently from `/browse` results at a glance,
  even before hitting the badge.
- Links to `/installed/{path_hash}`.

**`installed_detail.html`** (new template, structurally mirrors `model_detail.html`):
- Back link → `/installed`.
- Local install context block (path, file size, type, base model) shown at the top
  of the sidebar, above the CivitAI panel.
- If sidecar exists: main pane shows description + version info (reusing
  `_version_body.html` patterns where sensible), sidebar shows creator, rating,
  downloads, published date, license, "View on CivitAI" link (same `civitai-link`
  style as `model_detail.html`).
- If no sidecar: page renders local info only, with a "No CivitAI metadata
  available for this model" notice in place of the sidebar's CivitAI panel. No
  crash, no partial/broken sidebar.

## Route Changes (`main.py`)

- `POST /install` — extended to accept the CivitAI model context (passed from the
  calling template as hidden fields, matching the pattern `POST /download` already
  uses for its metadata fields). After starting the install job, spawns a
  server-side `asyncio.create_task(...)` that polls the job independently of the
  client (see "Server-side install-completion tracking" above) and writes the
  sidecar via a new `metadata_store.write_sidecar(...)` once it reaches a terminal
  success state. Install failures do not write a sidecar. The handler's HTTP
  response is unchanged — it still returns immediately with the initial job status,
  same as today.
- `GET /installed` — unchanged response shape at a glance, but now enriches each
  model dict with its sidecar data (or `None`) before rendering.
- `GET /installed/{path_hash}` — new route. Looks up the InvokeAI model by
  re-hashing `list_models()` entries' paths until one matches (no separate index
  needed since the list is small and already fetched). 404s via `render_error` if no
  match.

## New Module: `civitai_manager/metadata_store.py`

Mirrors the shape of `downloads.py`'s sidecar helpers:
- `write_sidecar(model_path: Path, metadata: dict) -> None`
- `read_sidecar(model_path: Path) -> dict | None` — returns `None` on missing or
  malformed JSON (logs a warning on malformed, does not raise).
- Internal: `_hash_path(model_path: Path) -> str`

## Error Handling & Edge Cases

- **Missing sidecar** (model installed outside this app, or predates this feature):
  card renders without badge/version label; detail page shows local-only info with
  an explanatory notice instead of a broken sidebar.
- **Malformed sidecar JSON**: treated identically to missing — logged as a warning,
  never crashes the page.
- **Path changes** (model directory moved/renamed after install): sidecar hash no
  longer matches; model reverts to local-only display until reinstalled through the
  app. Acceptable — no migration/repair path needed.
- **Install failure**: no sidecar written; nothing to roll back.
- **`civitai-manager` process restart mid-install** (e.g. via Server Admin): the
  in-memory background tracking task is lost along with it, so that one install's
  metadata won't be captured even though the InvokeAI install itself completes
  normally. Same class of gap as "path changes" — model reverts to local-only
  display until reinstalled through the app. No retry/recovery mechanism in this
  iteration.
- **Uninstall**: sidecar becomes orphaned (harmless, unreferenced file). No cleanup
  job in this iteration — acceptable technical debt, revisit only if
  `/workspace/civitai-metadata/` growth becomes a real problem.
- **CivitAI model later removed/changed upstream**: irrelevant to the detail page,
  since it renders from the captured sidecar snapshot, not a live fetch.

## Testing Notes

- Manual verification (no existing test suite in `civitai_manager/`): install a
  model from Browse, confirm sidecar appears in `/workspace/civitai-metadata/`,
  confirm `/installed` shows badge+version, confirm detail page renders full
  sidebar and "View on CivitAI" link resolves correctly. Then verify a
  pre-existing/manually-placed model (no sidecar) still renders on both pages
  without errors.
