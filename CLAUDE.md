# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

For any `runpodctl` usage (templates, pods, endpoints, network volumes), invoke the `runpodctl` skill instead of guessing flags — it has current command syntax for managing Runpod GPU workloads.

## Project Overview

This repo produces Docker images for running **InvokeAI** + **code-server** + a **CivitAI Manager** web app + a **Server Admin** dashboard on a RunPod GPU pod (RTX 5090 Blackwell or RTX 4090 Ada — two image variants, one Dockerfile each). Images are pushed to GitHub Container Registry (GHCR) via GitHub Actions and referenced in a RunPod template.

The original project goal was Stability Matrix — the repo name and GHCR image name (`stability-matrix-runpod`) reflect that, but the active implementation uses InvokeAI.

## Key Files

- [Dockerfile](Dockerfile) — builds the RTX 5090 image; installs system deps, code-server, AWS CLI, HuggingFace CLI, InvokeAI, then force-reinstalls cu130 PyTorch wheels
- [Dockerfile.4090](Dockerfile.4090) — builds the RTX 4090 image; uses the base image's torch 2.7.1 as-is (single-stage install, no force-reinstall — see Architecture Notes)
- [start.sh](start.sh) — container entrypoint (shared by both variants); injects CivitAI token into `/workspace/invokeai/invokeai.yaml`, then starts code-server (port 8080), `invokeai-web` (port 9090), and the CivitAI Manager (port 8000) via the Server Admin process supervisor, then starts the Server Admin web app itself (port 8001)
- [civitai_manager/](civitai_manager/) — FastAPI app for browsing/installing CivitAI models into InvokeAI; installed at `/opt/civitai_manager` (see Architecture Notes)
- [server_admin/](server_admin/) — FastAPI app: system/GPU/network telemetry dashboard, start/stop/restart control over the other 3 services, and a log viewer; installed at `/opt/server_admin` (see Architecture Notes)
- [.github/workflows/build.yml](.github/workflows/build.yml) — CI/CD: `build` (Dockerfile → `:main`) runs on every push to `main`; `build-4090` (Dockerfile.4090 → `:main-4090`) is manual-only (`workflow_dispatch`), since the 5090/default image covers ~99% of usage. Both push to `ghcr.io/spitznasio/stability-matrix-runpod`. Build cache for both jobs is a registry cache (`ghcr.io/.../stability-matrix-runpod:buildcache` / `:buildcache-4090`), not GHA's `type=gha` cache — the latter's 10GB repo-wide cap and slow blob writes were adding 40+ minutes to every 5090 build re-exporting cache layers.

## Build & Deploy Workflow

**Trigger a new 5090 image build:** push to `main` — GitHub Actions builds and pushes `:main`.

**Trigger a 4090 image build:** manual only — `gh workflow run build.yml` (or "Run workflow" in the Actions tab). A manual dispatch run always includes both `build` and `build-4090`; there's no way to run `build-4090` alone.

**Check build status:**

```bash
gh run list --workflow=build.yml
gh run view <run-id>
```

**Manage the RunPod template** (after a new image is published):

```bash
~/.local/bin/runpodctl template list
~/.local/bin/runpodctl template update --id <id> --imageName ghcr.io/spitznasio/stability-matrix-runpod:main
```

**Manage pods:**

```bash
~/.local/bin/runpodctl pod list
~/.local/bin/runpodctl pod start <pod-id>
~/.local/bin/runpodctl pod stop <pod-id>
```

## Architecture Notes

### Two-stage PyTorch install (critical, 5090 only)

In `Dockerfile`, InvokeAI is installed first with plain `pip install invokeai` so its dependency resolver can pick a compatible base torch version. A second `pip install --force-reinstall` then replaces those wheels with cu130 Blackwell builds (`torch==2.9.1+cu130`). Combining both steps into one causes pip resolution failures.

`Dockerfile.4090` deliberately does **not** do this: the base image already ships torch 2.7.1, which satisfies InvokeAI's `torch~=2.7.0` constraint, so a single `pip install invokeai` suffices. Don't "fix" the 4090 file to match the 5090's two-stage pattern — they differ on purpose.

### CivitAI Manager (port 8000)

A separate FastAPI app (`civitai_manager/`) for browsing and installing CivitAI models into InvokeAI. Installed at `/opt/civitai_manager` rather than `/workspace` because `/workspace` is overlaid by the RunPod volume disk at runtime, which would hide app code baked into the image. Login is optional: gated by `CIVITAI_MANAGER_USERNAME`/`CIVITAI_MANAGER_PASSWORD` (`civitai_manager/config.py`); if either is unset, the UI is unprotected. The session-signing secret (`CIVITAI_MANAGER_SESSION_SECRET`) falls back to a random value generated per process start when unset, so all sessions are invalidated on every container restart.

InvokeAI's `POST /api/v2/models/install` (v6.12.0/v6.13.0+) takes `source` and `access_token` as **query params** (plain strings), plus an optional `inplace` query param — the JSON body is reserved for `ModelRecordChanges` config override (description, trigger_phrases, source_url, etc.; send `{}` if unused). `invokeai_client.py`'s `install_model(source, access_token=None, *, inplace=False, config=None)` wraps this correctly. The `inplace` parameter is only used for local filesystem paths (the Downloads feature uses this to avoid duplicating large checkpoints).

When the manager's `/install` endpoint returns the generic "InvokeAI is not ready yet, or the install request was rejected" error, the real cause is hidden by a blanket `except httpx.HTTPError` in `main.py`. To see InvokeAI's actual response, curl it directly from inside the pod (code-server terminal, port 8080): `curl -i -X POST "http://localhost:9090/api/v2/models/install?source=<url-encoded-civitai-url>&inplace=false" -d '{}'`.

**InvokeAI 6.13.6 silently drops `trigger_phrases`/`source_url` from install-time config** (confirmed empirically on a live pod: a completed install job's `config_in` carries the values exactly as sent, but `config_out` — the actual resulting model record — has them as `null`; `name` and `description` apply correctly, only these two don't stick). Workaround: `InvokeAIClient.update_model_config(key, changes)` wraps `PATCH /api/v2/models/i/{key}`, which reliably applies them — call it after the install job completes, using `config_out.key`. `main._track_download_install` does this for installs from the Downloads page. `trigger_phrases` is also not a valid field at all for TextualInversion/embedding-type models (no such key in their schema) — sending it anyway is a harmless no-op there, so no type check is needed before calling `update_model_config`.

**Downloads feature** (`civitai_manager/downloads.py`): helper module for managing downloaded files and their CivitAI metadata sidecars. Routes: `POST /download` (queues a file via aria2 and writes metadata sidecar), `GET /downloads` (lists all files with install status), `POST /downloads/{filename}/install` (installs a file in-place with metadata). The sidecar JSON format is internal and versioned in `downloads.py`; changes to it should update the docstring there. File path validation in `/downloads/{filename}/install` uses `.resolve()` parent checks to guard against traversal — this is strict and correct, don't relax it for convenience.

**Installed page metadata** (`civitai_manager/metadata_store.py`): a second, independent sidecar mechanism from the Downloads feature above — capturing CivitAI metadata to `/workspace/civitai-metadata/<sha256(install_path)>.json`, read by `/installed` and `/installed/{path_hash}` to give installed models the same clickable-card-plus-detail-page experience as Browse. Written by a server-side background task that polls the InvokeAI install job independently of the client, so it isn't lost if the browser tab navigates away mid-install — `main._track_install_metadata` for installs via the direct "Install" button (`POST /install`), `main._track_download_install` for installs via the Downloads page (`POST /downloads/{filename}/install`); both share the polling loop (`_wait_for_completed_job`) and the sidecar-building logic (`_build_sidecar_metadata`). **These two install paths are separate code paths that both need to stay wired up** — a bug where Downloads-page installs showed no metadata on `/installed` happened because the sidecar capture was originally only added to the direct-Install path. Models without a sidecar (installed outside the app, or before this feature existed) still render, just without CivitAI metadata.

### Server Admin (port 8001)

A separate FastAPI app (`server_admin/`) — a simpler, container-focused Webmin alternative. Installed at `/opt/server_admin` for the same reason as CivitAI Manager (the `/workspace` volume disk overlay would hide it otherwise). It has its **own**, separate login from CivitAI Manager: gated by `SERVER_ADMIN_USERNAME`/`SERVER_ADMIN_PASSWORD` (`server_admin/config.py`), with its own session cookie (`server_admin_session`) and its own `SERVER_ADMIN_SESSION_SECRET` fallback behavior (random per process start if unset). **Because this app can stop/start the other services, leaving auth unset here is higher-risk than for CivitAI Manager** — set both env vars in any non-trivial deployment.

`server_admin/supervisor.py` is a pure-stdlib process supervisor (`ServiceManager`/`ManagedService`/`ServiceSpec`) that generalizes the PID-file pattern from `restart_invokeai.sh` to all managed services (InvokeAI, code-server, CivitAI Manager, the aria2 RPC daemon, and OneDrive Sync Manager — see `SERVICES` in `supervisor.py` for the current list). It tracks each service's PID file and log file under `/tmp/server-admin/` (not `/workspace` — these processes don't survive a pod restart anyway, so there's no need to persist supervisor state on the volume disk), and falls back to scanning `/proc/*/cmdline` to adopt a process that's running but whose PID file is missing or stale. `start.sh` launches every managed service through this supervisor (`python3 -m server_admin.supervisor start <key>`) instead of bare `&` backgrounding, so the dashboard's Services page has accurate status from boot and every service's logs are visible in the Logs page. Adding a new supervised service is just a new `ServiceSpec` entry in `SERVICES` plus a `python3 -m server_admin.supervisor start <key>` line in `start.sh` — Start/Stop/Restart/Status and log viewing come for free. `restart_invokeai.sh` is left in place as a manual escape hatch independent of the supervisor — it's now redundant for normal operation but still works as a fallback.

Each service also has a `.desired` marker file (`STATE_DIR / f"{key}.desired"`, `"running"` or `"stopped"`), written by `start()`/`stop()` — this is what lets `status()` distinguish "the user stopped this on purpose" from "it died on its own" (`crashed = not running and desired == "running"`). A background `monitor_loop()` (started as its own asyncio task in `main.py`'s lifespan, not piggybacked on page-poll requests — that would silently stop recovering services whenever no browser tab is open) periodically restarts any crashed service whose key is in the `SERVER_ADMIN_AUTO_RESTART` env-var allowlist. The Services page also shows live per-service CPU%/RSS via `ManagedService.resource_usage()`, backed by a `psutil.Process` cache keyed by pid (first read after a pid enters the cache always reports 0% — `psutil.Process.cpu_percent()`'s known behavior — real deltas appear from the next poll onward).

GPU telemetry (`server_admin/telemetry/gpu.py`) uses `pynvml` (the `nvidia-ml-py` package) rather than shelling out to `nvidia-smi` — `init_nvml()`/`shutdown_nvml()` run once from `main.py`'s lifespan, and any `NVMLError` (missing driver, or a driver restart mid-session) degrades to the same `{"available": False, "reason": ...}` shape the dashboard has always rendered as a "GPU unavailable" card, including when developing/testing this app on a machine without an NVIDIA GPU. Beyond the original utilization/VRAM/temperature/power fields, it now reports clock speeds and ECC error counts (`NVML_ERROR_NOT_SUPPORTED` on cards without ECC, e.g. the 4090/5090 this repo ships images for — but the field genuinely populates on datacenter cards like H100/H200/RTX PRO 6000/B200, since this dashboard isn't limited to the two consumer cards it's built for) and a per-process VRAM breakdown (`nvmlDeviceGetComputeRunningProcesses`/`GraphicsRunningProcesses`). Each process is cross-referenced against `service_manager.all_statuses()`'s pid map; where it matches a managed service, the dashboard's GPU card offers a "restart to free VRAM" button — this is deliberately just the existing `POST /services/{key}/restart` route, since there's no way to force-free GPU memory independent of the process holding it (`nvidia-smi --gpu-reset` isn't usable in a RunPod container, and RunPod's own pod-reset API wipes the whole container disk — not a VRAM-specific tool).

Network telemetry (`server_admin/telemetry/network.py`) computes throughput from a delta between `psutil.net_io_counters()` polls; **this only works correctly with a single uvicorn worker** — don't add `--workers >1` to the `uvicorn server_admin.main:app` invocation in `start.sh`, or each worker computes its own (wrong) delta.

Log search (`server_admin/logs.py`'s `search_log()`) is a separate, on-demand code path from the always-polled tail (`tail_log()`) — it streams the file forward line-by-line (substring or opt-in regex, both ANSI-stripped before matching, since InvokeAI's real logs are ANSI-colored) rather than reusing the tail's backward-chunk read, which is tail-specific. `/logs/download/{service}` streams the raw log file for offline viewing/grepping.

**Environment variable management** (`server_admin/env_vars.py`, `/environment` page): a curated `REGISTRY` of `EnvVarSpec` entries (app-relevant vars plus CUDA vars — not all of `os.environ`, to keep the exposed surface bounded) backs a view/edit UI. Edits are written to `/workspace/server-admin/env-overrides.env` (`KEY=value`, `shlex.quote`d) — a new file on the volume disk, distinct from the supervisor's ephemeral `/tmp/server-admin/` state dir — and `start.sh` sources it as the very first thing it does, so overrides win over the pod's originally injected values and survive a pod restart. Saving also live-applies the value to Server Admin's own `os.environ` immediately, so restarting the owning service (`EnvVarSpec.owner_service`, a `server_admin.supervisor.SERVICES` key) via the existing `POST /services/{key}/restart` picks up the change right away without a pod restart. Vars with `owner_service=None` — `SERVER_ADMIN_*` (Server Admin isn't itself a supervised service, so it can't restart itself) and `ARIA2_RPC_SECRET` (baked into `aria2-rpc`'s launch command at `supervisor.py` import time, so a supervised restart wouldn't actually pick up a new value — see that `ServiceSpec`'s `start_cmd`) — are pod-restart-only; the UI shows a static note instead of a restart button for these. Sensitive values are masked by default and only sent to the browser in full via an explicit reveal action. Only registry keys are editable — there's no way to add an arbitrary new env var name through this UI.

### aria2 download-to-folder (port 8000, "Download to folder" button)

A second, independent download path alongside the "Install" button's InvokeAI-API flow. InvokeAI's own installer is single-connection and has no real resume story, so large checkpoints downloaded that way are slow and fragile. This path instead uses `aria2c` (already installed in both Docker images) run as a **persistent RPC daemon** — an entry (`aria2-rpc`) in `server_admin/supervisor.py`'s `SERVICES` dict, so it gets start/stop/restart and log viewing on the Server Admin dashboard for free, same as the other managed services. `start.sh` generates and exports `ARIA2_RPC_SECRET` before launching any supervised process so both `aria2-rpc` and `civitai-manager` (the only RPC client) inherit the same secret via `os.environ`.

`civitai_manager/aria2_client.py` talks to the daemon over local JSON-RPC (`http://127.0.0.1:6800/jsonrpc`, never exposed as a container port) to queue downloads (`aria2.addUri`) and poll progress (`aria2.tellStatus`), using aria2's real capabilities: 16-way segmented multi-connection transfer, automatic resume via aria2's own `.aria2` control files, and SHA256 checksum verification against the hash CivitAI publishes per file. Files land in `CIVITAI_DOWNLOAD_DIR` (default `/workspace/civitai-downloads`) as plain files. The UI/polling pattern (`_download_status.html`, `/download/{gid}/status`) mirrors the existing install-job pattern (`_install_status.html`) almost exactly.

When a file is downloaded through the app, a sidecar JSON file (`<filename>.civitai.json`) is written next to it, capturing CivitAI metadata: model id/name/type, base model, description (as plain text), trigger words (for LoRAs), and a link back to the CivitAI model page. Files without sidecars (pre-existing or manually placed in the folder) are still usable but lose that metadata. The new **Downloads page** (`/downloads` nav tab) lists all files in the folder, shows whether each is installed in InvokeAI, and offers one-click install with metadata import — no need to manually import via InvokeAI's "Scan Folder" anymore. Install happens `inplace=true` (no duplicate copy), and the description and trigger words (critical for LoRAs) are carried over automatically into InvokeAI's model record via the install call's `ModelRecordChanges` body.

### InvokeAI config injection

`start.sh` reads `/workspace/invokeai/invokeai.yaml` at container start, not at image build time, so the CivitAI and HuggingFace tokens (set as RunPod env vars `CIVITAI_API_TOKEN` / `HF_TOKEN`) are written into its `remote_api_tokens` list (matched by URL regex: `civitai.com`/`civitai.red` for CivitAI, `huggingface.co` for HuggingFace) before the server launches. This is InvokeAI's own model manager's only way to authenticate downloads — it does not read `HF_TOKEN` itself; that var only authenticates standalone `huggingface-cli`/`huggingface_hub` usage in a terminal, not InvokeAI's internal downloader. The config file lives on the volume disk and persists across pod restarts.

### Volume disk at /workspace

All InvokeAI state (`INVOKEAI_ROOT=/workspace/invokeai`) lives on the RunPod volume disk, not the ephemeral container layer. This means models and outputs survive pod restarts but the pod must restart on the same physical host that holds the volume.

### Ports

- `8080` — code-server (VS Code in browser)
- `8000` — CivitAI Manager web app
- `9090` — InvokeAI web UI
- `8001` — Server Admin dashboard
- `8002` — OneDrive Sync Manager web app
- `22` — Full SSH (public IP, key auth, SCP/SFTP-capable). `start.sh` starts `sshd` and injects RunPod's `$PUBLIC_KEY` into `authorized_keys` — our custom `ENTRYPOINT` replaces the base image's own entrypoint, which is what normally handles this, so it has to be done explicitly. This is separate from RunPod's proxied "basic SSH" (no setup needed, but no SCP/SFTP) — see [Connect to a Pod with SSH](https://docs.runpod.io/pods/configuration/use-ssh). **The RunPod template must expose `22/tcp` and the pod must have a public IP for this to work** — neither is automatic from the Docker image alone; update the template's ports and (if changing an existing pod) recreate the pod, since port exposure isn't applied to already-running pods.

## Environment Variables (set in RunPod, not hardcoded)

| Variable | Purpose |
| --- | --- |
| `CIVITAI_API_TOKEN` | Injected into `invokeai.yaml`'s `remote_api_tokens` by `start.sh`, so InvokeAI's own downloads from CivitAI are authenticated |
| `HF_TOKEN` | Injected into `invokeai.yaml`'s `remote_api_tokens` by `start.sh`, so InvokeAI's own downloads of gated/private HuggingFace models are authenticated. Also picked up automatically by standalone `huggingface-cli`/`huggingface_hub` usage in a terminal (that library reads `HF_TOKEN` directly) — but that's a separate mechanism from the `invokeai.yaml` injection, which InvokeAI's model manager needs instead |
| `PYTORCH_CUDA_ALLOC_CONF` | Set to `backend:cudaMallocAsync` in image; override with `max_split_size_mb:512,expandable_segments:True` if OOM during tiling |
| `CUDA_CACHE_MAXSIZE` | `4294967296` (4 GB shader cache) |
| `HF_HUB_ENABLE_HF_TRANSFER` | Enables Rust-based fast transfer for HuggingFace downloads |
| `CIVITAI_MANAGER_USERNAME` / `CIVITAI_MANAGER_PASSWORD` | Login for the CivitAI Manager UI (port 8000); unset either to disable login |
| `CIVITAI_MANAGER_SESSION_SECRET` | Signs the CivitAI Manager session cookie; if unset, a random secret is generated per process start (sessions reset on restart) |
| `CIVITAI_MANAGER_LOG_LEVEL` | Verbosity of CivitAI Manager's own log messages (visible via Server Admin's log viewer, service key `civitai-manager`); default `INFO`, set to `DEBUG` for per-request detail |
| `SERVER_ADMIN_USERNAME` / `SERVER_ADMIN_PASSWORD` | Login for the Server Admin UI (port 8001); unset either to disable login. **This app can stop/start services — leaving it open is higher-risk than CivitAI Manager.** |
| `SERVER_ADMIN_SESSION_SECRET` | Signs the Server Admin session cookie; if unset, a random secret is generated per process start (sessions reset on restart) |
| `SERVER_ADMIN_AUTO_RESTART` | Comma-separated allowlist of service keys to auto-restart when they crash (die without being stopped via the dashboard), e.g. `invokeai,aria2-rpc`; empty by default (opt-in per service) |
| `SERVER_ADMIN_CRASH_MONITOR_INTERVAL_S` | How often (seconds) the background monitor checks for crashed services; default `10` |
| `SERVER_ADMIN_MAX_LOG_TAIL_LINES` | Upper bound on the `lines` query param for `/logs` and `/logs/tail`, to cap the cost of the backward-chunked tail read; default `5000` |
| `CIVITAI_DOWNLOAD_DIR` | Destination folder for the "Download to folder" path; default `/workspace/civitai-downloads` |
| `ARIA2_RPC_SECRET` | Shared secret between the aria2 RPC daemon and CivitAI Manager; generated per boot by `start.sh` if unset |

## Blackwell / RTX 5090 Requirements

- Base image: `runpod/pytorch:1.0.3-cu1300-torch291-ubuntu2404`
- PyTorch index: `https://download.pytorch.org/whl/cu130`
- Driver: 580.x+; CUDA: 13.0
- Python: 3.12+
