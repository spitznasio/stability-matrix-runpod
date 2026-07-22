# AGENTS.md

This file helps AI coding agents work effectively in this repository.

## Scope

- This repo builds and publishes two Docker images for RunPod:
  - `Dockerfile` -> RTX 5090 image tag `:main` (builds on every push to `main`)
  - `Dockerfile.4090` -> RTX 4090 image tag `:main-4090` (manual `workflow_dispatch` only)
- Runtime stack: InvokeAI + code-server + CivitAI Manager (FastAPI, port 8000) + Server Admin (FastAPI, port 8001) + OneDrive Sync Manager (port 8002), all launched by [start.sh](start.sh) through a process supervisor ([server_admin/supervisor.py](server_admin/supervisor.py)).

Start with [CLAUDE.md](CLAUDE.md) for architecture and operational context.

## Start Here

- [CLAUDE.md](CLAUDE.md): project architecture, key files, and critical gotchas.
- [README.md](README.md): user-facing setup and usage.
- [README-RUNPOD.md](README-RUNPOD.md): RunPod deployment flow.
- [IMPORTANT.md](IMPORTANT.md): Blackwell/5090 caveats.
- [CONFIG-FORMAT.md](CONFIG-FORMAT.md): required YAML layout details.

## Build, Test, and Validation

- There is no formal test/lint suite in this repo.
- Primary validation is Docker build success and runtime sanity.
- CI is defined in [.github/workflows/build.yml](.github/workflows/build.yml):
  - Push to `main` builds and pushes both image variants.
- Useful local checks:
  - `docker build -f Dockerfile .`
  - `docker build -f Dockerfile.4090 .`

## Critical Invariants (Do Not Break)

- Keep the 5090 two-stage PyTorch install pattern in [Dockerfile](Dockerfile).
  - Do not collapse it into one `pip install` step.
- Do not "harmonize" [Dockerfile.4090](Dockerfile.4090) with the 5090 flow.
  - The difference is intentional.
- Keep InvokeAI install API usage in [civitai_manager/invokeai_client.py](civitai_manager/invokeai_client.py) as query params (`source`, optional `access_token`, optional `inplace`) with a JSON `config` body (ModelRecordChanges, or `{}` if unused).
- Keep CivitAI Manager (`/opt/civitai_manager`) and Server Admin (`/opt/server_admin`) installed outside `/workspace` because `/workspace` is a mounted volume at runtime.
- Preserve `remote_api_tokens` key order/shape written by [start.sh](start.sh); see [CONFIG-FORMAT.md](CONFIG-FORMAT.md).
- In [civitai_manager/downloads.py](civitai_manager/downloads.py), the sidecar JSON format (`<filename>.civitai.json`) is internal. Do not change field names/types without also updating the read/write logic and the route handlers.
- Path validation in [civitai_manager/main.py](civitai_manager/main.py)'s `/downloads/{filename}/install` endpoint uses `.resolve().parent` checks to prevent traversal attacks — keep this strict, don't weaken it.
- All services must be launched via the supervisor (`python3 -m server_admin.supervisor start <key>` in [start.sh](start.sh)), not bare `&` backgrounding, so [server_admin/](server_admin/)'s Services/Logs pages stay accurate.
- Don't add `--workers >1` to the `uvicorn server_admin.main:app` invocation — [server_admin/telemetry/network.py](server_admin/telemetry/network.py)'s throughput calculation depends on a single worker's `psutil` delta.
- Don't relax the `/downloads`-page install-time sidecar tracking — Downloads-page installs and direct-`/install`-button installs are separate code paths ([civitai_manager/main.py](civitai_manager/main.py)'s `_track_download_install` and `_track_install_metadata`) that both need to stay wired to [civitai_manager/metadata_store.py](civitai_manager/metadata_store.py).
- Keep [server_admin/env_vars.py](server_admin/env_vars.py)'s `REGISTRY` as the only editable env var surface — don't let `/environment` grow a path to add arbitrary new keys. If a var's `owner_service` is `None`, don't wire a restart button for it (it's pod-restart-only on purpose — either it's a `SERVER_ADMIN_*` var, or, like `ARIA2_RPC_SECRET`, its owning process only reads it at supervisor-import time).

## Ports

- `8080` code-server, `9090` InvokeAI, `8000` CivitAI Manager, `8001` Server Admin, `8002` OneDrive Sync Manager, `22` full SSH (public IP + key auth, set up explicitly by [start.sh](start.sh) since the custom `ENTRYPOINT` replaces the base image's own).
- CivitAI Manager and Server Admin have separate, independent logins (separate env vars, session cookies, and session-secret fallback behavior). Server Admin can stop/start the other services, so leaving its auth unset is higher-risk.

## Editing Guidelines

- Prefer minimal, targeted changes; avoid broad refactors.
- When changing startup/runtime behavior, review [start.sh](start.sh), [civitai_manager/main.py](civitai_manager/main.py), and [server_admin/supervisor.py](server_admin/supervisor.py) together.
- When changing Docker or versioning, review both [Dockerfile](Dockerfile) and [Dockerfile.4090](Dockerfile.4090) plus [.github/workflows/build.yml](.github/workflows/build.yml).
- When modifying the Downloads feature: changes to sidecar format require updates to [civitai_manager/downloads.py](civitai_manager/downloads.py), the `/download` handler, and the `/downloads/{filename}/install` handler in [civitai_manager/main.py](civitai_manager/main.py). Update the template [civitai_manager/templates/_install_panel.html](civitai_manager/templates/_install_panel.html) if the metadata fields passed to `/download` change.
- When adding a new managed service, add a `ServiceSpec` to `SERVICES` in [server_admin/supervisor.py](server_admin/supervisor.py) and a corresponding `python3 -m server_admin.supervisor start <key>` line in [start.sh](start.sh) — Start/Stop/Restart/Status and log viewing come for free.

## Debugging Notes

- If `/install` or `/downloads/{filename}/install` shows a generic InvokeAI readiness/rejection error in [civitai_manager/main.py](civitai_manager/main.py), verify the underlying InvokeAI response directly from the pod terminal as described in [CLAUDE.md](CLAUDE.md).
- If the `/downloads` page doesn't show expected files: check that `CIVITAI_DOWNLOAD_DIR` (default `/workspace/civitai-downloads`) contains the files, that any `.aria2` control files have been cleaned up (they indicate in-progress downloads), and that corrupt sidecar JSON doesn't crash the listing (it's logged as a warning but doesn't fail the page).
- If trigger words aren't carried over during install: verify the sidecar JSON has a non-empty `trigger_words` list, that the `/downloads/{filename}/install` route receives it, and that `invokeai_client.install_model()` is called with `config={'trigger_phrases': [...],...}` — check the logs for the actual config sent to InvokeAI.
