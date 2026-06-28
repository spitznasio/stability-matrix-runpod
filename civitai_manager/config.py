import os
import secrets

CIVITAI_API_TOKEN = os.environ.get("CIVITAI_API_TOKEN")
CIVITAI_BASE_URL = os.environ.get("CIVITAI_BASE_URL", "https://civitai.com/api/v1")
INVOKEAI_BASE_URL = os.environ.get("INVOKEAI_BASE_URL", "http://localhost:9090")

# Login for the CivitAI Manager UI itself. Unset by default — set both as
# RunPod env vars to require login; if either is missing, login is disabled.
AUTH_USERNAME = os.environ.get("CIVITAI_MANAGER_USERNAME")
AUTH_PASSWORD = os.environ.get("CIVITAI_MANAGER_PASSWORD")
# Signs the session cookie. If not set explicitly, a random secret is generated
# per process start, so existing sessions are simply invalidated on restart.
SESSION_SECRET = os.environ.get("CIVITAI_MANAGER_SESSION_SECRET", secrets.token_hex(32))
