import os
import secrets

CIVITAI_API_TOKEN = os.environ.get("CIVITAI_API_TOKEN")
CIVITAI_BASE_URL = os.environ.get("CIVITAI_BASE_URL", "https://civitai.com/api/v1")
INVOKEAI_BASE_URL = os.environ.get("INVOKEAI_BASE_URL", "http://localhost:9090")

# Controls verbosity of the app's own log messages (visible via Server Admin's
# log viewer, service key "civitai-manager"). DEBUG adds per-request detail
# (outbound CivitAI/InvokeAI calls, cache hit/miss); INFO covers installs,
# downloads, and auth events.
LOG_LEVEL = os.environ.get("CIVITAI_MANAGER_LOG_LEVEL", "INFO")

# Login for the CivitAI Manager UI itself. Unset by default — set both as
# RunPod env vars to require login; if either is missing, login is disabled.
AUTH_USERNAME = os.environ.get("CIVITAI_MANAGER_USERNAME")
AUTH_PASSWORD = os.environ.get("CIVITAI_MANAGER_PASSWORD")
# Signs the session cookie. If not set explicitly, a random secret is generated
# per process start, so existing sessions are simply invalidated on restart.
SESSION_SECRET = os.environ.get("CIVITAI_MANAGER_SESSION_SECRET", secrets.token_hex(32))

# In-memory cache for CivitAI API responses, to stay under CivitAI's rate limits.
CIVITAI_CACHE_TTL_SECONDS = int(os.environ.get("CIVITAI_CACHE_TTL_SECONDS", "3600"))
CIVITAI_CACHE_MAXSIZE = int(os.environ.get("CIVITAI_CACHE_MAXSIZE", "500"))

# "Download to folder" path: files land here for manual import via InvokeAI's
# own "Scan Folder", instead of going through InvokeAI's (slow, single-
# connection) install API. Downloads are driven by a local aria2 RPC daemon
# (see server_admin/supervisor.py's "aria2-rpc" service) for multi-connection
# transfer, resume, and checksum verification.
CIVITAI_DOWNLOAD_DIR = os.environ.get("CIVITAI_DOWNLOAD_DIR", "/workspace/civitai-downloads")
ARIA2_RPC_URL = os.environ.get("ARIA2_RPC_URL", "http://127.0.0.1:6800/jsonrpc")
ARIA2_RPC_SECRET = os.environ.get("ARIA2_RPC_SECRET", "")

# Sidecar metadata captured for models installed via the app's "Install"
# button — see metadata_store.py. Lives on the volume disk (like
# CIVITAI_DOWNLOAD_DIR) so it survives pod restarts.
CIVITAI_METADATA_DIR = os.environ.get("CIVITAI_METADATA_DIR", "/workspace/civitai-metadata")
