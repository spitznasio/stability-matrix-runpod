# InvokeAI on RunPod (5090 & 4090)

Run **InvokeAI** (Stable Diffusion), **code-server** (VS Code), and a **CivitAI Model Manager** together on RunPod's RTX 5090 or 4090 GPUs. The Docker image is built and deployed automatically — just point a RunPod template at it and start a pod.

**Features:**

- 🎨 Full InvokeAI web UI for text-to-image, image editing, and more
- 🤖 CivitAI Manager — browse and install models directly into InvokeAI
- ☁️ OneDrive Sync Manager — manual one-way sync from pod-local folders to OneDrive
- 💻 code-server — VS Code in your browser for terminal access and scripting
- 📦 Pre-installed tools: AWS CLI, HuggingFace CLI, aria2 for fast downloads
- 💾 Persistent volume storage — models and images survive pod restarts

---

## Quick Start (5 minutes)

### 1. Create a RunPod pod

1. Go to [RunPod](https://www.runpod.io)
2. **Create a new pod** with these settings:
   - **Image**: `ghcr.io/spitznasio/stability-matrix-runpod:main` (5090) or `:main-4090` (4090)
   - **GPU**: RTX 5090 (Blackwell) or RTX 4090 (Ada)
   - **Container Disk**: 10 GB minimum
   - **Volume Disk**: 100+ GB (for models and outputs)
  - **Port Mapping**: Expose `8080`, `8000`, `9090`, `8002` as HTTP

### 2. Set environment variables

In the pod's **Environment** section, set:

```
CIVITAI_API_TOKEN=your_token_here
```

(Get a token from [CivitAI Settings](https://civitai.com/user/account) → API Keys.)

**Optional:**

- `CIVITAI_MANAGER_USERNAME` and `CIVITAI_MANAGER_PASSWORD` — if both are set, the CivitAI Manager UI requires login
- `CIVITAI_MANAGER_SESSION_SECRET` — signs the session cookie; if unset, sessions reset on restart
- `ONEDRIVE_MANAGER_USERNAME` — local auth username for OneDrive Sync Manager
- `ONEDRIVE_MANAGER_PASSWORD_HASH` — bcrypt hash for local auth password
- `ONEDRIVE_MANAGER_SESSION_SECRET` — session-signing secret for OneDrive Sync Manager
- `ONEDRIVE_CLIENT_ID` — Microsoft app registration client ID
- `ONEDRIVE_REDIRECT_URI` — callback URL, e.g. `https://<pod-id>-8002.proxy.runpod.net/auth/callback`
- `ONEDRIVE_TENANT_ID` — optional, defaults to `common`
- `ONEDRIVE_SCOPES` — optional, defaults to `offline_access Files.ReadWrite.All User.Read`
- `ONEDRIVE_SYNC_LOCAL_BASE_ROOT` — optional, defaults to `/workspace`

### 3. Start the pod

Click **Start Pod**. Wait ~2 minutes for services to boot. RunPod will show proxy URLs for ports 8080, 8000, 9090, and 8002.

Done! All three services are now running.

---

## Using the Services

### InvokeAI — Generate Images

1. Click the **9090** proxy link (or `https://<pod-id>-9090.proxy.runpod.net`)
2. Enter a text prompt (e.g., "a cat wearing sunglasses")
3. Click **Generate**
4. Download your image or view history

[InvokeAI docs](https://invoke-ai.github.io/InvokeAI/) for advanced features.

### CivitAI Manager — Install Models

The CivitAI Manager lets you search and install models without leaving RunPod.

1. Click the **8000** proxy link (or `https://<pod-id>-8000.proxy.runpod.net`)
2. **Search** for a model (e.g., "Pony Diffusion")
3. Click the model to see versions and details
4. Click **Install** on a version — it downloads and registers with InvokeAI
5. Refresh InvokeAI and the model appears in the model selector

**Tip:** The CivitAI Manager respects your API token for faster downloads.

### code-server — Terminal & Scripts

1. Click the **8080** proxy link (or `https://<pod-id>-8080.proxy.runpod.net`)
2. You're in VS Code. Open a terminal: `Ctrl+`` (backtick) or **Terminal** → **New Terminal**
3. Navigate to `/workspace` to access:
   - `/workspace/invokeai` — InvokeAI data, models, outputs
   - `/workspace` — scripts for downloading/uploading to S3, restarting InvokeAI, etc.

### OneDrive Sync Manager — Manual Sync

1. Open the **8002** proxy link (`https://<pod-id>-8002.proxy.runpod.net`).
2. Sign in with your local OneDrive Sync Manager credentials.
3. Click **Connect OneDrive** and complete OAuth.
4. Use **Dry-Run** and **Start Sync Job** for one-way local-to-OneDrive uploads.

---

## Common Tasks

### Download a model via CivitAI Manager (easiest)

See **Using the Services > CivitAI Manager** above.

### Download models from HuggingFace or external sources

Use code-server's terminal:

```bash
cd /workspace/invokeai/models/checkpoints

# HuggingFace (fast with HF_HUB_ENABLE_HF_TRANSFER=1, already set)
huggingface-cli download username/model-name model.safetensors --local-dir .

# Generic HTTP with aria2
aria2c -x 16 -s 16 "https://example.com/model.safetensors"
```

### Restart InvokeAI

If InvokeAI crashes or you need a clean reload:

```bash
bash /workspace/restart_invokeai.sh
```

### Backup models or images to S3

```bash
# Back up generated images
python /workspace/upload_images_to_s3.py --bucket my-bucket --prefix invokeai/images

# Back up models
python /workspace/upload_models_to_s3.py \
  --bucket my-bucket \
  --prefix invokeai/models \
  --source /workspace/invokeai/models
```

Requires AWS credentials (set `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` as env vars).

### Restore models from S3

```bash
python /workspace/download_from_s3_skip_existing.py \
  --bucket my-bucket \
  --prefix invokeai/models \
  --dest /workspace/invokeai/models
```

---

## Stopping & Restarting

### Stop the pod

Click **Stop Pod** in RunPod. Models, images, and all configuration persist on the volume disk — nothing is lost.

### Restart the pod

Click **Start Pod**. The same volume disk reattaches, and all three services boot again with your models intact.

**⚠️ Important:** The volume disk is tied to a specific physical host. If you stop the pod and the 5090/4090 is fully rented out in that datacenter, you may not be able to restart on the same volume.

---

## Configuration

### CivitAI token injection

When the container starts, `start.sh` reads `CIVITAI_API_TOKEN` from the environment and writes it into `/workspace/invokeai/invokeai.yaml` so InvokeAI can download from CivitAI without auth issues. This happens automatically — you don't need to configure it manually.

### Memory/performance tuning

The image sets these environment variables by default:

| Var | Value | When to override |
| --- | --- | --- |
| `PYTORCH_CUDA_ALLOC_CONF` | `backend:cudaMallocAsync` | If you hit OOM errors, try `max_split_size_mb:512,expandable_segments:True` |
| `CUDA_CACHE_MAXSIZE` | `4294967296` (4GB) | Rarely needed |
| `HF_HUB_ENABLE_HF_TRANSFER` | `1` | Already optimized for fast HF downloads |

To override, set them as RunPod env vars before starting the pod.

---

## Troubleshooting

### CivitAI Manager won't load / shows an error

1. Check that the pod is running and all three ports (8080, 8000, 9090) are exposed.
2. Wait ~30 seconds and refresh — services take time to boot.
3. Check the pod's **Logs** tab for errors.

### InvokeAI is slow to generate

- Ensure you're using the correct image variant for your GPU (`:main` for 5090, `:main-4090` for 4090).
- On first generation with a new model, warm-up is slow (compiling kernels). Subsequent generations are faster.
- Check **Settings** → **Performance** in InvokeAI to adjust batch size and memory allocation.

### Models keep disappearing after restart

Models are stored on the volume disk at `/workspace/invokeai/models`, so they should persist. If they're disappearing, the volume disk may have detached or reset. Check RunPod's volume status.

### I can't restart my pod on the same volume

The volume is tied to a specific physical host. If that host is fully booked, create a new pod with a different volume or contact RunPod support.

---

## Technical Details (For Developers)

### Image variants

Two Dockerfiles for different GPUs:

| Image | GPU | PyTorch | CUDA | Notes |
| --- | --- | --- | --- | --- |
| `stability-matrix-runpod:main` | RTX 5090 (Blackwell) | 2.9.1 | 13.0 | Supports sm_120 compute capability |
| `stability-matrix-runpod:main-4090` | RTX 4090 (Ada) | 2.7.1 | 12.8.1 | Stable, widely used |

### Build & deploy

Push to `main` → GitHub Actions builds both variants and pushes to GHCR:

```bash
# Trigger a build
git push origin main

# Check build status
gh run list --workflow=build.yml
```

After a build completes, update your RunPod template:

```bash
~/.local/bin/runpodctl template update --id <template-id> \
  --imageName ghcr.io/spitznasio/stability-matrix-runpod:main
```

### Volume disk architecture

- **Path:** `/workspace` is a RunPod-managed persistent volume disk
- **Data:** Models (`/workspace/invokeai/models`), outputs (`/workspace/invokeai/outputs`), config (`/workspace/invokeai/invokeai.yaml`) all live here
- **App code:** `civitai_manager` is installed at `/opt` (outside the volume mount) so it's not hidden when the volume attaches

---

## References

- [InvokeAI Documentation](https://invoke-ai.github.io/InvokeAI/)
- [CivitAI](https://civitai.com)
- [RunPod](https://www.runpod.io)
- [Project README](CLAUDE.md) — technical architecture & file guide
