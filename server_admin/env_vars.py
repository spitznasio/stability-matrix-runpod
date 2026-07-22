"""Curated registry of the env vars Server Admin's Environment page can show
and edit, plus the read/write logic for persisting edits to
/workspace/server-admin/env-overrides.env (sourced by start.sh on boot, so
edits survive a pod restart) and live-applying them to this process's
os.environ (so a supervised service restart picks them up immediately).
"""

import os
import shlex
from dataclasses import dataclass
from pathlib import Path

# SERVER_ADMIN_ENV_OVERRIDES_DIR lets this be pointed at a writable dir
# during local development, where /workspace doesn't exist — same pattern as
# supervisor.py's SERVER_ADMIN_STATE_DIR. Left unset in a real pod, where
# /workspace is the volume disk.
OVERRIDES_DIR = Path(os.environ.get("SERVER_ADMIN_ENV_OVERRIDES_DIR", "/workspace/server-admin"))
OVERRIDES_FILE = OVERRIDES_DIR / "env-overrides.env"

# Snapshot of the environment as this process actually started with, used by
# clear_value() to revert an override back to whatever the container had
# before this module made any edits of its own.
_ORIGINAL_ENV: dict[str, str] = dict(os.environ)


@dataclass(frozen=True)
class EnvVarSpec:
    key: str
    category: str
    description: str
    sensitive: bool = False
    default: str | None = None
    # SERVICES key (server_admin.supervisor) to restart to apply an edit, or
    # None if this var can only take effect after a full pod restart (either
    # it belongs to Server Admin itself, which isn't a supervised service, or
    # the owning process reads it at a point a supervised restart can't
    # reach — see ARIA2_RPC_SECRET below).
    owner_service: str | None = None


REGISTRY: list[EnvVarSpec] = [
    # --- CivitAI Manager ---
    EnvVarSpec(
        key="CIVITAI_API_TOKEN",
        category="CivitAI Manager",
        description="CivitAI API token used for authenticated downloads and searches.",
        sensitive=True,
        owner_service="civitai-manager",
    ),
    EnvVarSpec(
        key="CIVITAI_BASE_URL",
        category="CivitAI Manager",
        description="Base URL for the CivitAI API.",
        default="https://civitai.com/api/v1",
        owner_service="civitai-manager",
    ),
    EnvVarSpec(
        key="INVOKEAI_BASE_URL",
        category="CivitAI Manager",
        description="Base URL CivitAI Manager uses to reach InvokeAI's API.",
        default="http://localhost:9090",
        owner_service="civitai-manager",
    ),
    EnvVarSpec(
        key="CIVITAI_MANAGER_LOG_LEVEL",
        category="CivitAI Manager",
        description="Log verbosity for CivitAI Manager's own log messages.",
        default="INFO",
        owner_service="civitai-manager",
    ),
    EnvVarSpec(
        key="CIVITAI_MANAGER_USERNAME",
        category="CivitAI Manager",
        description="Login username for the CivitAI Manager UI. Unset either this or the password to disable login.",
        owner_service="civitai-manager",
    ),
    EnvVarSpec(
        key="CIVITAI_MANAGER_PASSWORD",
        category="CivitAI Manager",
        description="Login password for the CivitAI Manager UI.",
        sensitive=True,
        owner_service="civitai-manager",
    ),
    EnvVarSpec(
        key="CIVITAI_MANAGER_SESSION_SECRET",
        category="CivitAI Manager",
        description="Signs the CivitAI Manager session cookie. Changing it logs out all active sessions.",
        sensitive=True,
        owner_service="civitai-manager",
    ),
    EnvVarSpec(
        key="CIVITAI_CACHE_TTL_SECONDS",
        category="CivitAI Manager",
        description="How long CivitAI API responses are cached, in seconds.",
        default="3600",
        owner_service="civitai-manager",
    ),
    EnvVarSpec(
        key="CIVITAI_CACHE_MAXSIZE",
        category="CivitAI Manager",
        description="Maximum number of cached CivitAI API responses.",
        default="500",
        owner_service="civitai-manager",
    ),
    EnvVarSpec(
        key="CIVITAI_DOWNLOAD_DIR",
        category="CivitAI Manager",
        description='Destination folder for the "Download to folder" aria2 path.',
        default="/workspace/civitai-downloads",
        owner_service="civitai-manager",
    ),
    EnvVarSpec(
        key="CIVITAI_METADATA_DIR",
        category="CivitAI Manager",
        description="Where Installed-page CivitAI metadata sidecars are written.",
        default="/workspace/civitai-metadata",
        owner_service="civitai-manager",
    ),
    EnvVarSpec(
        key="ARIA2_RPC_URL",
        category="CivitAI Manager",
        description="URL CivitAI Manager uses to reach the local aria2 RPC daemon.",
        default="http://127.0.0.1:6800/jsonrpc",
        owner_service="civitai-manager",
    ),
    # --- aria2 / Downloads ---
    EnvVarSpec(
        key="ARIA2_RPC_SECRET",
        category="aria2 / Downloads",
        description=(
            "Shared secret between the aria2 RPC daemon and CivitAI Manager. "
            "The aria2 daemon only reads this at its own process start "
            "(baked into its launch command), so restarting it here would "
            "not actually pick up a new value — requires a full pod restart."
        ),
        sensitive=True,
        owner_service=None,
    ),
    # --- InvokeAI / CUDA ---
    EnvVarSpec(
        key="PYTORCH_CUDA_ALLOC_CONF",
        category="InvokeAI / CUDA",
        description="PyTorch CUDA allocator tuning, e.g. max_split_size_mb:512,expandable_segments:True to work around OOM during tiling.",
        default="backend:cudaMallocAsync",
        owner_service="invokeai",
    ),
    EnvVarSpec(
        key="CUDA_CACHE_MAXSIZE",
        category="InvokeAI / CUDA",
        description="Size of the CUDA shader cache, in bytes.",
        default="4294967296",
        owner_service="invokeai",
    ),
    EnvVarSpec(
        key="CUDA_VISIBLE_DEVICES",
        category="InvokeAI / CUDA",
        description='Restricts which GPUs are visible to InvokeAI, e.g. "0" on a multi-GPU pod.',
        owner_service="invokeai",
    ),
    EnvVarSpec(
        key="HF_HUB_ENABLE_HF_TRANSFER",
        category="InvokeAI / CUDA",
        description="Enables the Rust-based fast transfer backend for HuggingFace downloads.",
        owner_service="invokeai",
    ),
    # --- OneDrive Sync Manager ---
    EnvVarSpec(
        key="ONEDRIVE_MANAGER_USERNAME",
        category="OneDrive Sync Manager",
        description="Login username for the OneDrive Sync Manager UI.",
        owner_service="onedrive-sync",
    ),
    EnvVarSpec(
        key="ONEDRIVE_MANAGER_PASSWORD_HASH",
        category="OneDrive Sync Manager",
        description="Hashed login password for the OneDrive Sync Manager UI.",
        sensitive=True,
        owner_service="onedrive-sync",
    ),
    EnvVarSpec(
        key="ONEDRIVE_MANAGER_SESSION_SECRET",
        category="OneDrive Sync Manager",
        description="Signs the OneDrive Sync Manager session cookie. Changing it logs out all active sessions.",
        sensitive=True,
        owner_service="onedrive-sync",
    ),
    EnvVarSpec(
        key="ONEDRIVE_CLIENT_ID",
        category="OneDrive Sync Manager",
        description="Azure AD application (client) ID used for OneDrive OAuth.",
        owner_service="onedrive-sync",
    ),
    EnvVarSpec(
        key="ONEDRIVE_TENANT_ID",
        category="OneDrive Sync Manager",
        description="Azure AD tenant ID used for OneDrive OAuth.",
        default="common",
        owner_service="onedrive-sync",
    ),
    EnvVarSpec(
        key="ONEDRIVE_SCOPES",
        category="OneDrive Sync Manager",
        description="Space-separated OAuth scopes requested from Microsoft Graph.",
        owner_service="onedrive-sync",
    ),
    EnvVarSpec(
        key="ONEDRIVE_SYNC_LOCAL_BASE_ROOT",
        category="OneDrive Sync Manager",
        description="Local base directory sync jobs are rooted under.",
        default="/workspace",
        owner_service="onedrive-sync",
    ),
    EnvVarSpec(
        key="ONEDRIVE_SYNC_MAX_RETRIES",
        category="OneDrive Sync Manager",
        description="Maximum retry attempts for a failed sync job.",
        default="3",
        owner_service="onedrive-sync",
    ),
    EnvVarSpec(
        key="ONEDRIVE_SYNC_JOB_HISTORY_MAX_JOBS",
        category="OneDrive Sync Manager",
        description="Maximum number of past sync jobs kept in history.",
        default="250",
        owner_service="onedrive-sync",
    ),
    EnvVarSpec(
        key="ONEDRIVE_SYNC_JOB_MAX_EVENTS",
        category="OneDrive Sync Manager",
        description="Maximum number of events kept per sync job.",
        default="200",
        owner_service="onedrive-sync",
    ),
    EnvVarSpec(
        key="ONEDRIVE_MANAGER_LOG_LEVEL",
        category="OneDrive Sync Manager",
        description="Log verbosity for OneDrive Sync Manager's own log messages.",
        default="INFO",
        owner_service="onedrive-sync",
    ),
    # --- Server Admin ---
    EnvVarSpec(
        key="SERVER_ADMIN_USERNAME",
        category="Server Admin",
        description="Login username for this dashboard. Unset either this or the password to disable login.",
        owner_service=None,
    ),
    EnvVarSpec(
        key="SERVER_ADMIN_PASSWORD",
        category="Server Admin",
        description="Login password for this dashboard.",
        sensitive=True,
        owner_service=None,
    ),
    EnvVarSpec(
        key="SERVER_ADMIN_SESSION_SECRET",
        category="Server Admin",
        description="Signs this dashboard's session cookie. Changing it logs out all active sessions.",
        sensitive=True,
        owner_service=None,
    ),
    EnvVarSpec(
        key="SERVER_ADMIN_AUTO_RESTART",
        category="Server Admin",
        description="Comma-separated service keys to auto-restart when they crash, e.g. invokeai,aria2-rpc.",
        owner_service=None,
    ),
    EnvVarSpec(
        key="SERVER_ADMIN_CRASH_MONITOR_INTERVAL_S",
        category="Server Admin",
        description="How often, in seconds, the background monitor checks for crashed services.",
        default="10",
        owner_service=None,
    ),
    EnvVarSpec(
        key="SERVER_ADMIN_MAX_LOG_TAIL_LINES",
        category="Server Admin",
        description="Upper bound on the lines query param for the Logs page.",
        default="5000",
        owner_service=None,
    ),
]

_BY_KEY: dict[str, EnvVarSpec] = {spec.key: spec for spec in REGISTRY}


def get_spec(key: str) -> EnvVarSpec:
    return _BY_KEY[key]


def categories() -> list[tuple[str, list[EnvVarSpec]]]:
    """REGISTRY grouped by category, preserving registry order."""
    grouped: dict[str, list[EnvVarSpec]] = {}
    for spec in REGISTRY:
        grouped.setdefault(spec.category, []).append(spec)
    return list(grouped.items())


def current_value(key: str) -> str | None:
    return os.environ.get(key)


def mask(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "•" * len(value)
    return "•" * (len(value) - 4) + value[-4:]


def _read_overrides() -> dict[str, str]:
    if not OVERRIDES_FILE.exists():
        return {}
    overrides: dict[str, str] = {}
    for line in OVERRIDES_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw_value = line.partition("=")
        parts = shlex.split(raw_value)
        overrides[key] = parts[0] if parts else ""
    return overrides


def _write_overrides(overrides: dict[str, str]) -> None:
    OVERRIDES_DIR.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={shlex.quote(value)}" for key, value in sorted(overrides.items())]
    content = "\n".join(lines) + ("\n" if lines else "")
    tmp_path = OVERRIDES_FILE.with_suffix(".tmp")
    tmp_path.write_text(content)
    tmp_path.replace(OVERRIDES_FILE)


def has_override(key: str) -> bool:
    return key in _read_overrides()


def set_value(key: str, value: str) -> None:
    """Raises KeyError for an unknown key. Live-applies to os.environ before
    attempting to persist, so a persistence failure (e.g. /workspace not
    mounted) still leaves the in-memory value updated — an OSError from this
    function means "applied live, but not saved to disk", not "not applied".
    """
    if key not in _BY_KEY:
        raise KeyError(key)
    os.environ[key] = value
    overrides = _read_overrides()
    overrides[key] = value
    _write_overrides(overrides)


def clear_value(key: str) -> None:
    """Raises KeyError for an unknown key. Same live-apply-before-persist
    ordering as set_value() — see its docstring.
    """
    if key not in _BY_KEY:
        raise KeyError(key)
    original = _ORIGINAL_ENV.get(key)
    if original is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = original
    overrides = _read_overrides()
    overrides.pop(key, None)
    _write_overrides(overrides)
