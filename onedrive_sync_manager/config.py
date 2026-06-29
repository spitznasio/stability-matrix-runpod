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
ONEDRIVE_REDIRECT_URI = os.environ.get("ONEDRIVE_REDIRECT_URI")
ONEDRIVE_SCOPES = [
    scope.strip()
    for scope in os.environ.get("ONEDRIVE_SCOPES", "offline_access Files.ReadWrite.All User.Read").split()
    if scope.strip()
]
SYNC_LOCAL_BASE_ROOT = os.environ.get("ONEDRIVE_SYNC_LOCAL_BASE_ROOT", "/workspace")
SYNC_MAX_RETRIES = int(os.environ.get("ONEDRIVE_SYNC_MAX_RETRIES", "3"))


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
    if not ONEDRIVE_REDIRECT_URI:
        missing.append("ONEDRIVE_REDIRECT_URI")
    if missing:
        raise RuntimeError(
            "OneDrive delegated OAuth config incomplete. Missing env vars: " + ", ".join(missing)
        )


def verify_password(plain_password: str) -> bool:
    if not AUTH_PASSWORD_HASH:
        return False
    return pwd_context.verify(plain_password, AUTH_PASSWORD_HASH)
