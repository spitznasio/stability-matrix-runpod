# InvokeAI on RunPod (5090 & 4090)

Run **InvokeAI** (Stable Diffusion), **code-server** (VS Code), and a **CivitAI Model Manager** together in one pod. Pick the image variant that matches your GPU, set one environment variable, and start generating.

**Included:**

- 🎨 InvokeAI web UI — text-to-image, image editing, and more
- 🤖 CivitAI Manager — browse and install models without leaving RunPod
- 💻 code-server — VS Code in your browser for terminal access and scripting
- 📦 AWS CLI, HuggingFace CLI, aria2 pre-installed for fast model downloads
- 💾 Persistent volume storage — models and images survive pod restarts

---

## 1. Pick your image

| GPU | Image tag |
| --- | --- |
| RTX 5090 (Blackwell) | `ghcr.io/spitznasio/stability-matrix-runpod:main` |
| RTX 4090 (Ada) | `ghcr.io/spitznasio/stability-matrix-runpod:main-4090` |

If you're deploying from the RunPod template, this is already set — just make sure the GPU you select matches the tag.

**⚠️ Important (5090 variant only):** the 5090 image requires CUDA 13.0 drivers. When deploying the template, on the pod type selection screen click **Additional Filters** and set the **CUDA** filter to **13.0** — otherwise RunPod may offer a host whose driver can't run this image.

## 2. Pod settings

- **Container Disk**: 10 GB minimum
- **Volume Disk**: 100+ GB, mounted at `/workspace` (holds models, outputs, and config — survives restarts)
- **Exposed HTTP Ports**: `8080`, `8000`, `9090`, `8002`

## 3. Environment variables

| Variable | Required? | Purpose |
| --- | --- | --- |
| `CIVITAI_API_TOKEN` | Recommended | Lets InvokeAI and the CivitAI Manager download from CivitAI without auth errors. Get one from [CivitAI Settings](https://civitai.com/user/account) → API Keys. |
| `CIVITAI_MANAGER_USERNAME` / `CIVITAI_MANAGER_PASSWORD` | Optional | Set both to require login on the CivitAI Manager UI (port 8000). Leave either unset to leave it open. |
| `CIVITAI_MANAGER_SESSION_SECRET` | Optional | Signs the CivitAI Manager session cookie. If unset, a new one is generated on every restart, which logs everyone out. |
| `ONEDRIVE_MANAGER_USERNAME` | Required for OneDrive Sync Manager | Local login username for the OneDrive Sync Manager UI (port 8002). |
| `ONEDRIVE_MANAGER_PASSWORD_HASH` | Required for OneDrive Sync Manager | Bcrypt hash for local login password. Generate with `python -c "from passlib.context import CryptContext; print(CryptContext(schemes=['bcrypt']).hash('your-password'))"`. |
| `ONEDRIVE_MANAGER_SESSION_SECRET` | Recommended | Signs the OneDrive Sync Manager session cookie. If unset, a new one is generated on every restart and all sessions are invalidated. |
| `ONEDRIVE_CLIENT_ID` | Required for OneDrive Sync Manager | App registration client ID used for delegated OAuth sign-in. |
| `ONEDRIVE_REDIRECT_URI` | Required for OneDrive Sync Manager | OAuth redirect URI. Use your pod URL, e.g. `https://<POD_ID>-8002.proxy.runpod.net/auth/callback`. |
| `ONEDRIVE_TENANT_ID` | Optional | Defaults to `common`. Set to your tenant ID for single-tenant auth. |
| `ONEDRIVE_SCOPES` | Optional | Defaults to `offline_access Files.ReadWrite.All User.Read`. Override only if needed. |
| `ONEDRIVE_SYNC_LOCAL_BASE_ROOT` | Optional | Defaults to `/workspace`. Limits local sync path selection to this root. |
| `PYTORCH_CUDA_ALLOC_CONF` | Optional | Defaults to `backend:cudaMallocAsync`. If you hit out-of-memory errors during generation, try `max_split_size_mb:512,expandable_segments:True` instead. |

## 4. Start the pod

Click **Start Pod**. Wait ~2 minutes for services to boot, then open the proxy links RunPod shows for ports `8080`, `8000`, `9090`, and `8002`.

---

## Using the services

### InvokeAI (port 9090) — generate images

1. Open the **9090** proxy link.
2. Enter a prompt and click **Generate**.
3. View or download results from the history panel.

See the [InvokeAI docs](https://invoke-ai.github.io/InvokeAI/) for advanced features.

### CivitAI Manager (port 8000) — install models

1. Open the **8000** proxy link.
2. Search for a model (e.g. "Pony Diffusion").
3. Click a model to see its versions, then click **Install** on the version you want.
4. Refresh InvokeAI — the model appears in the model selector.

Installs use your `CIVITAI_API_TOKEN` automatically, so downloads are faster and gated content works.

### code-server (port 8080) — terminal access

1. Open the **8080** proxy link — this is VS Code running in your browser.
2. Open a terminal with `` Ctrl+` `` or **Terminal → New Terminal**.
3. Useful paths:
   - `/workspace/invokeai` — models, outputs, and `invokeai.yaml` config
   - `/workspace` — helper scripts (S3 backup/restore, InvokeAI restart)

### OneDrive Sync Manager (port 8002) — manual local-to-OneDrive sync

1. Open the **8002** proxy link.
2. Sign in using `ONEDRIVE_MANAGER_USERNAME` and the password corresponding to `ONEDRIVE_MANAGER_PASSWORD_HASH`.
3. Click **Connect OneDrive** to complete Microsoft OAuth.
4. Run **Dry-Run** to preview uploads from `/workspace` (or your configured base root).
5. Click **Start Sync Job** to run one-way upload sync.

---

## Common tasks

### Download a model manually (HuggingFace or direct URL)

```bash
cd /workspace/invokeai/models/checkpoints

# HuggingFace (fast transfer is already enabled)
huggingface-cli download username/model-name model.safetensors --local-dir .

# Any direct URL
aria2c -x 16 -s 16 "https://example.com/model.safetensors"
```

### Restart InvokeAI without restarting the pod

```bash
bash /workspace/restart_invokeai.sh
```

### Back up models or generated images to S3

```bash
python /workspace/upload_images_to_s3.py --bucket my-bucket --prefix invokeai/images
python /workspace/upload_models_to_s3.py --bucket my-bucket --prefix invokeai/models --source /workspace/invokeai/models
```

Requires `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` set as env vars.

### Restore models from S3 into a fresh pod

```bash
python /workspace/download_from_s3_skip_existing.py --bucket my-bucket --prefix invokeai/models --dest /workspace/invokeai/models
```

---

## Stopping & restarting

- **Stop Pod**: models, images, and config persist on the volume disk — nothing is lost.
- **Start Pod**: the same volume reattaches and all three services come back up automatically.

**⚠️ Heads up:** the volume disk is tied to a specific physical host. If that host's 5090s/4090s are fully rented out when you try to restart, you may not be able to reattach to your existing volume. If this happens, contact RunPod support or start a fresh pod with a new volume.

---

## Troubleshooting

**CivitAI Manager won't load / shows an error**
Confirm all three ports (8080, 8000, 9090) are exposed, then wait ~30 seconds and refresh — services take time to boot. Check the pod's **Logs** tab for errors.

**InvokeAI is slow to generate**
Make sure you picked the image tag matching your GPU (`:main` for 5090, `:main-4090` for 4090). The first generation after startup is slow due to kernel compilation — later ones are faster.

**Models keep disappearing after a restart**
Models live on the volume disk at `/workspace/invokeai/models` and should persist. If they vanish, check that your volume actually reattached — see "Stopping & restarting" above.

**Out-of-memory errors during generation**
Set `PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:512,expandable_segments:True` as a pod env var and restart.

---

## References

- [InvokeAI Documentation](https://invoke-ai.github.io/InvokeAI/)
- [CivitAI](https://civitai.com)
- [RunPod](https://www.runpod.io)
