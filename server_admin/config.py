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
MAX_LOG_TAIL_LINES = int(os.environ.get("SERVER_ADMIN_MAX_LOG_TAIL_LINES", "5000"))
POLL_INTERVAL_S = os.environ.get("SERVER_ADMIN_POLL_INTERVAL_S", "3")

# Comma-separated allowlist of service keys to auto-restart when they crash
# (i.e. die without having been stopped via the dashboard). Empty by default
# — auto-restart is opt-in per service, e.g. "invokeai,aria2-rpc".
AUTO_RESTART_SERVICES = {
    s.strip() for s in os.environ.get("SERVER_ADMIN_AUTO_RESTART", "").split(",") if s.strip()
}
CRASH_MONITOR_INTERVAL_S = int(os.environ.get("SERVER_ADMIN_CRASH_MONITOR_INTERVAL_S", "10"))
