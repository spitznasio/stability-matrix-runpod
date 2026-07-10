import os
import secrets

from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

AUTH_USERNAME = os.environ.get("ONEDRIVE_MANAGER_USERNAME")
AUTH_PASSWORD_HASH = os.environ.get("ONEDRIVE_MANAGER_PASSWORD_HASH")
SESSION_SECRET = os.environ.get("ONEDRIVE_MANAGER_SESSION_SECRET", secrets.token_hex(32))
SESSION_COOKIE = "onedrive_sync_session"
ONEDRIVE_CLIENT_ID = os.environ.get("ONEDRIVE_CLIENT_ID")
ONEDRIVE_TENANT_ID = os.environ.get("ONEDRIVE_TENANT_ID", "common")
_DEFAULT_ONEDRIVE_SCOPES = "Files.ReadWrite.AppFolder User.Read"
_MSAL_RESERVED_SCOPES = {"offline_access", "openid", "profile"}
_raw_scopes = [
    scope.strip()
    for scope in os.environ.get("ONEDRIVE_SCOPES", _DEFAULT_ONEDRIVE_SCOPES).split()
    if scope.strip()
]
ONEDRIVE_SCOPES = [scope for scope in _raw_scopes if scope.lower() not in _MSAL_RESERVED_SCOPES]
if not ONEDRIVE_SCOPES:
    ONEDRIVE_SCOPES = _DEFAULT_ONEDRIVE_SCOPES.split()
SYNC_LOCAL_BASE_ROOT = os.environ.get("ONEDRIVE_SYNC_LOCAL_BASE_ROOT", "/workspace")
SYNC_MAX_RETRIES = int(os.environ.get("ONEDRIVE_SYNC_MAX_RETRIES", "3"))
JOB_HISTORY_MAX_JOBS = max(1, int(os.environ.get("ONEDRIVE_SYNC_JOB_HISTORY_MAX_JOBS", "250")))
JOB_HISTORY_MAX_EVENTS_PER_JOB = max(10, int(os.environ.get("ONEDRIVE_SYNC_JOB_MAX_EVENTS", "200")))

# Controls verbosity of the app's own log messages (visible via Server Admin's
# log viewer, service key "onedrive-sync"). DEBUG adds per-request detail;
# INFO covers auth, sync job lifecycle, and per-file upload retries/failures.
LOG_LEVEL = os.environ.get("ONEDRIVE_MANAGER_LOG_LEVEL", "INFO")


def validate_required_auth_config() -> None:
    missing = []
    if not AUTH_USERNAME:
        missing.append("ONEDRIVE_MANAGER_USERNAME")
    if not AUTH_PASSWORD_HASH:
        missing.append("ONEDRIVE_MANAGER_PASSWORD_HASH")
    if missing:
        raise RuntimeError(
            "OneDrive Sync Manager requires local auth. Missing env vars: " + ", ".join(missing)
        )


def validate_required_oauth_config() -> None:
    missing = []
    if not ONEDRIVE_CLIENT_ID:
        missing.append("ONEDRIVE_CLIENT_ID")
    if missing:
        raise RuntimeError(
            "OneDrive delegated OAuth config incomplete. Missing env vars: " + ", ".join(missing)
        )


def verify_password(plain_password: str) -> bool:
    if not AUTH_PASSWORD_HASH:
        return False
    return pwd_context.verify(plain_password, AUTH_PASSWORD_HASH)
