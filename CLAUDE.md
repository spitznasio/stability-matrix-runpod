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
- [.github/workflows/build.yml](.github/workflows/build.yml) — CI/CD: two parallel jobs, `build` (Dockerfile → `:main`) and `build-4090` (Dockerfile.4090 → `:main-4090`), both pushed to `ghcr.io/spitznasio/stability-matrix-runpod` on every push to `main`

## Build & Deploy Workflow

**Trigger a new image build:** push to `main` — GitHub Actions builds and pushes both variants in parallel: `:main` (5090, from `Dockerfile`) and `:main-4090` (4090, from `Dockerfile.4090`).

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

InvokeAI's `POST /api/v2/models/install` (v6.12.0/v6.13.0) takes `source` and `access_token` as **query params** (plain strings), not a JSON body object — the JSON body is reserved for an optional `ModelRecordChanges` config override (send `{}` if unused). `invokeai_client.py`'s `install_model()` sends this correctly; don't "fix" it back to a `{"source": {...}}` body shape.

When the manager's `/install` endpoint returns the generic "InvokeAI is not ready yet, or the install request was rejected" error, the real cause is hidden by a blanket `except httpx.HTTPError` in `main.py`. To see InvokeAI's actual response, curl it directly from inside the pod (code-server terminal, port 8080): `curl -i -X POST "http://localhost:9090/api/v2/models/install?source=<url-encoded-civitai-url>" -d '{}'`.

### Server Admin (port 8001)

A separate FastAPI app (`server_admin/`) — a simpler, container-focused Webmin alternative. Installed at `/opt/server_admin` for the same reason as CivitAI Manager (the `/workspace` volume disk overlay would hide it otherwise). It has its **own**, separate login from CivitAI Manager: gated by `SERVER_ADMIN_USERNAME`/`SERVER_ADMIN_PASSWORD` (`server_admin/config.py`), with its own session cookie (`server_admin_session`) and its own `SERVER_ADMIN_SESSION_SECRET` fallback behavior (random per process start if unset). **Because this app can stop/start the other services, leaving auth unset here is higher-risk than for CivitAI Manager** — set both env vars in any non-trivial deployment.

`server_admin/supervisor.py` is a pure-stdlib process supervisor (`ServiceManager`/`ManagedService`/`ServiceSpec`) that generalizes the PID-file pattern from `restart_invokeai.sh` to all three managed services (InvokeAI, code-server, CivitAI Manager). It tracks each service's PID file and log file under `/tmp/server-admin/` (not `/workspace` — these processes don't survive a pod restart anyway, so there's no need to persist supervisor state on the volume disk), and falls back to scanning `/proc/*/cmdline` to adopt a process that's running but whose PID file is missing or stale. `start.sh` now launches all three managed services through this supervisor (`python3 -m server_admin.supervisor start <key>`) instead of bare `&` backgrounding, so the dashboard's Services page has accurate status from boot. `restart_invokeai.sh` is left in place as a manual escape hatch independent of the supervisor — it's now redundant for normal operation but still works as a fallback.

GPU telemetry (`server_admin/telemetry/gpu.py`) shells out to `nvidia-smi`; if it's missing or fails, the dashboard renders a "GPU unavailable" card instead of erroring — this is the same code path used when developing/testing this app on a machine without an NVIDIA GPU. Network telemetry (`server_admin/telemetry/network.py`) computes throughput from a delta between `psutil.net_io_counters()` polls; **this only works correctly with a single uvicorn worker** — don't add `--workers >1` to the `uvicorn server_admin.main:app` invocation in `start.sh`, or each worker computes its own (wrong) delta.

### InvokeAI config injection

`start.sh` reads `/workspace/invokeai/invokeai.yaml` at container start, not at image build time, so the CivitAI token (set as a RunPod env var `CIVITAI_API_TOKEN`) is written before the server launches. The config file lives on the volume disk and persists across pod restarts.

### Volume disk at /workspace

All InvokeAI state (`INVOKEAI_ROOT=/workspace/invokeai`) lives on the RunPod volume disk, not the ephemeral container layer. This means models and outputs survive pod restarts but the pod must restart on the same physical host that holds the volume.

### Ports

- `8080` — code-server (VS Code in browser)
- `8000` — CivitAI Manager web app
- `9090` — InvokeAI web UI
- `8001` — Server Admin dashboard

## Environment Variables (set in RunPod, not hardcoded)

| Variable | Purpose |
| --- | --- |
| `CIVITAI_API_TOKEN` | Injected into `invokeai.yaml` by `start.sh` |
| `PYTORCH_CUDA_ALLOC_CONF` | Set to `backend:cudaMallocAsync` in image; override with `max_split_size_mb:512,expandable_segments:True` if OOM during tiling |
| `CUDA_CACHE_MAXSIZE` | `4294967296` (4 GB shader cache) |
| `HF_HUB_ENABLE_HF_TRANSFER` | Enables Rust-based fast transfer for HuggingFace downloads |
| `CIVITAI_MANAGER_USERNAME` / `CIVITAI_MANAGER_PASSWORD` | Login for the CivitAI Manager UI (port 8000); unset either to disable login |
| `CIVITAI_MANAGER_SESSION_SECRET` | Signs the CivitAI Manager session cookie; if unset, a random secret is generated per process start (sessions reset on restart) |
| `SERVER_ADMIN_USERNAME` / `SERVER_ADMIN_PASSWORD` | Login for the Server Admin UI (port 8001); unset either to disable login. **This app can stop/start services — leaving it open is higher-risk than CivitAI Manager.** |
| `SERVER_ADMIN_SESSION_SECRET` | Signs the Server Admin session cookie; if unset, a random secret is generated per process start (sessions reset on restart) |

## Blackwell / RTX 5090 Requirements

- Base image: `runpod/pytorch:1.0.3-cu1300-torch291-ubuntu2404`
- PyTorch index: `https://download.pytorch.org/whl/cu130`
- Driver: 580.x+; CUDA: 13.0
- Python: 3.12+
