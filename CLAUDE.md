# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

For any `runpodctl` usage (templates, pods, endpoints, network volumes), invoke the `runpodctl` skill instead of guessing flags — it has current command syntax for managing Runpod GPU workloads.

## Project Overview

This repo produces Docker images for running **InvokeAI** + **code-server** + a **CivitAI Manager** web app on a RunPod GPU pod (RTX 5090 Blackwell or RTX 4090 Ada — two image variants, one Dockerfile each). Images are pushed to GitHub Container Registry (GHCR) via GitHub Actions and referenced in a RunPod template.

The original project goal was Stability Matrix — the repo name and GHCR image name (`stability-matrix-runpod`) reflect that, but the active implementation uses InvokeAI.

## Key Files

- [Dockerfile](Dockerfile) — builds the RTX 5090 image; installs system deps, code-server, AWS CLI, HuggingFace CLI, InvokeAI, then force-reinstalls cu130 PyTorch wheels
- [Dockerfile.4090](Dockerfile.4090) — builds the RTX 4090 image; uses the base image's torch 2.7.1 as-is (single-stage install, no force-reinstall — see Architecture Notes)
- [start.sh](start.sh) — container entrypoint (shared by both variants); injects CivitAI token into `/workspace/invokeai/invokeai.yaml`, then starts code-server (port 8080), `invokeai-web` (port 9090), and the CivitAI Manager (port 8000) in the background
- [civitai_manager/](civitai_manager/) — FastAPI app for browsing/installing CivitAI models into InvokeAI; installed at `/opt/civitai_manager` (see Architecture Notes)
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

### InvokeAI config injection

`start.sh` reads `/workspace/invokeai/invokeai.yaml` at container start, not at image build time, so the CivitAI token (set as a RunPod env var `CIVITAI_API_TOKEN`) is written before the server launches. The config file lives on the volume disk and persists across pod restarts.

### Volume disk at /workspace

All InvokeAI state (`INVOKEAI_ROOT=/workspace/invokeai`) lives on the RunPod volume disk, not the ephemeral container layer. This means models and outputs survive pod restarts but the pod must restart on the same physical host that holds the volume.

### Ports

- `8080` — code-server (VS Code in browser)
- `8000` — CivitAI Manager web app
- `9090` — InvokeAI web UI

## Environment Variables (set in RunPod, not hardcoded)

| Variable | Purpose |
| --- | --- |
| `CIVITAI_API_TOKEN` | Injected into `invokeai.yaml` by `start.sh` |
| `PYTORCH_CUDA_ALLOC_CONF` | Set to `backend:cudaMallocAsync` in image; override with `max_split_size_mb:512,expandable_segments:True` if OOM during tiling |
| `CUDA_CACHE_MAXSIZE` | `4294967296` (4 GB shader cache) |
| `HF_HUB_ENABLE_HF_TRANSFER` | Enables Rust-based fast transfer for HuggingFace downloads |
| `CIVITAI_MANAGER_USERNAME` / `CIVITAI_MANAGER_PASSWORD` | Login for the CivitAI Manager UI (port 8000); unset either to disable login |
| `CIVITAI_MANAGER_SESSION_SECRET` | Signs the CivitAI Manager session cookie; if unset, a random secret is generated per process start (sessions reset on restart) |

## Blackwell / RTX 5090 Requirements

- Base image: `runpod/pytorch:1.0.3-cu1300-torch291-ubuntu2404`
- PyTorch index: `https://download.pytorch.org/whl/cu130`
- Driver: 580.x+; CUDA: 13.0
- Python: 3.12+
