import os
import secrets

# Login for the Server Admin UI. Unset by default — set both as RunPod env
# vars to require login; if either is missing, login is disabled. This app
# can stop/start services, so leaving it open is higher-risk than CivitAI
# Manager's equivalent setting.
AUTH_USERNAME = os.environ.get("SERVER_ADMIN_USERNAME")
AUTH_PASSWORD = os.environ.get("SERVER_ADMIN_PASSWORD")
# Signs the session cookie. If not set explicitly, a random secret is
# generated per process start, so existing sessions are invalidated on restart.
SESSION_SECRET = os.environ.get("SERVER_ADMIN_SESSION_SECRET", secrets.token_hex(32))

LOG_TAIL_LINES = int(os.environ.get("SERVER_ADMIN_LOG_TAIL_LINES", "200"))
POLL_INTERVAL_S = os.environ.get("SERVER_ADMIN_POLL_INTERVAL_S", "3")
