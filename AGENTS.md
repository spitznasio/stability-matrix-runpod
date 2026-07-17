# AGENTS.md

This file helps AI coding agents work effectively in this repository.

## Scope

- This repo builds and publishes two Docker images for RunPod:
  - `Dockerfile` -> RTX 5090 image tag `:main`
  - `Dockerfile.4090` -> RTX 4090 image tag `:main-4090`
- Runtime stack: InvokeAI + code-server + CivitAI Manager (FastAPI).

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
- Keep CivitAI Manager installed outside `/workspace` (currently `/opt/civitai_manager`) because `/workspace` is a mounted volume at runtime.
- Preserve `remote_api_tokens` key order/shape written by [start.sh](start.sh); see [CONFIG-FORMAT.md](CONFIG-FORMAT.md).
- In [civitai_manager/downloads.py](civitai_manager/downloads.py), the sidecar JSON format (`<filename>.civitai.json`) is internal. Do not change field names/types without also updating the read/write logic and the route handlers.
- Path validation in [civitai_manager/main.py](civitai_manager/main.py)'s `/downloads/{filename}/install` endpoint uses `.resolve().parent` checks to prevent traversal attacks — keep this strict, don't weaken it.

## Editing Guidelines

- Prefer minimal, targeted changes; avoid broad refactors.
- When changing startup/runtime behavior, review [start.sh](start.sh) and [civitai_manager/main.py](civitai_manager/main.py) together.
- When changing Docker or versioning, review both [Dockerfile](Dockerfile) and [Dockerfile.4090](Dockerfile.4090) plus [.github/workflows/build.yml](.github/workflows/build.yml).
- When modifying the Downloads feature: changes to sidecar format require updates to [civitai_manager/downloads.py](civitai_manager/downloads.py), the `/download` handler, and the `/downloads/{filename}/install` handler in [civitai_manager/main.py](civitai_manager/main.py). Update the template [civitai_manager/templates/_install_panel.html](civitai_manager/templates/_install_panel.html) if the metadata fields passed to `/download` change.

## Debugging Notes

- If `/install` or `/downloads/{filename}/install` shows a generic InvokeAI readiness/rejection error in [civitai_manager/main.py](civitai_manager/main.py), verify the underlying InvokeAI response directly from the pod terminal as described in [CLAUDE.md](CLAUDE.md).
- If the `/downloads` page doesn't show expected files: check that `CIVITAI_DOWNLOAD_DIR` (default `/workspace/civitai-downloads`) contains the files, that any `.aria2` control files have been cleaned up (they indicate in-progress downloads), and that corrupt sidecar JSON doesn't crash the listing (it's logged as a warning but doesn't fail the page).
- If trigger words aren't carried over during install: verify the sidecar JSON has a non-empty `trigger_words` list, that the `/downloads/{filename}/install` route receives it, and that `invokeai_client.install_model()` is called with `config={'trigger_phrases': [...],...}` — check the logs for the actual config sent to InvokeAI.
