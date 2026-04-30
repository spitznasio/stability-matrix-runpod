# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This repo produces a Docker image for running **InvokeAI** + **code-server** on a RunPod RTX 5090 (Blackwell / sm_120) GPU pod. The image is pushed to GitHub Container Registry (GHCR) via GitHub Actions and referenced in a RunPod template.

The original project goal was Stability Matrix — the repo name and GHCR image name (`stability-matrix-runpod`) reflect that, but the active implementation uses InvokeAI.

## Key Files

- [Dockerfile](Dockerfile) — builds the image; installs system deps, code-server, AWS CLI, HuggingFace CLI, InvokeAI, then force-reinstalls cu130 PyTorch wheels
- [start.sh](start.sh) — container entrypoint; injects CivitAI token into `/workspace/invokeai/invokeai.yaml`, then starts code-server (port 8080) and `invokeai-web` (port 9090) in the background
- [.github/workflows/build.yml](.github/workflows/build.yml) — CI/CD: builds and pushes to `ghcr.io/spitznasio/stability-matrix-runpod` on every push to `main`

## Build & Deploy Workflow

**Trigger a new image build:** push to `main` — GitHub Actions does the rest.

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

### Two-stage PyTorch install (critical)
InvokeAI is installed first with plain `pip install invokeai` so its dependency resolver can pick a compatible base torch version. A second `pip install --force-reinstall` then replaces those wheels with cu130 Blackwell builds (`torch==2.9.1+cu130`). Combining both steps into one causes pip resolution failures.

### InvokeAI config injection
`start.sh` reads `/workspace/invokeai/invokeai.yaml` at container start, not at image build time, so the CivitAI token (set as a RunPod env var `CIVITAI_API_TOKEN`) is written before the server launches. The config file lives on the volume disk and persists across pod restarts.

### Volume disk at /workspace
All InvokeAI state (`INVOKEAI_ROOT=/workspace/invokeai`) lives on the RunPod volume disk, not the ephemeral container layer. This means models and outputs survive pod restarts but the pod must restart on the same physical host that holds the volume.

### Ports
- `8080` — code-server (VS Code in browser)
- `9090` — InvokeAI web UI

## Environment Variables (set in RunPod, not hardcoded)

| Variable | Purpose |
|---|---|
| `CIVITAI_API_TOKEN` | Injected into `invokeai.yaml` by `start.sh` |
| `PYTORCH_CUDA_ALLOC_CONF` | Set to `backend:cudaMallocAsync` in image; override with `max_split_size_mb:512,expandable_segments:True` if OOM during tiling |
| `CUDA_CACHE_MAXSIZE` | `4294967296` (4 GB shader cache) |
| `HF_HUB_ENABLE_HF_TRANSFER` | Enables Rust-based fast transfer for HuggingFace downloads |

## Blackwell / RTX 5090 Requirements

- Base image: `runpod/pytorch:1.0.3-cu1300-torch291-ubuntu2404`
- PyTorch index: `https://download.pytorch.org/whl/cu130`
- Driver: 580.x+; CUDA: 13.0
- Python: 3.12+

See [IMPORTANT.md](IMPORTANT.md) (gitignored, local only) for operational gotchas around GDDR7 fragmentation, sm_120 kernel mismatches, and code-server venv inheritance.
