import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from . import config

logger = logging.getLogger(__name__)


def path_hash(model_path: str) -> str:
    return hashlib.sha256(model_path.encode("utf-8")).hexdigest()


def _sidecar_path(model_path: str) -> Path:
    return Path(config.CIVITAI_METADATA_DIR) / f"{path_hash(model_path)}.json"


def write_sidecar(model_path: str, metadata: dict) -> None:
    target = _sidecar_path(model_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(metadata, indent=2))


def read_sidecar(model_path: str) -> dict | None:
    target = _sidecar_path(model_path)
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text())
    except (OSError, json.JSONDecodeError):
        logger.warning("Failed to read metadata sidecar %s", target, exc_info=True)
        return None


def _error_path(model_path: str) -> Path:
    # Separate suffix from _sidecar_path's .json — a background-task failure
    # must never be able to overwrite or block reading the real metadata.
    return Path(config.CIVITAI_METADATA_DIR) / f"{path_hash(model_path)}.error.json"


def write_background_error(model_path: str, message: str) -> None:
    target = _error_path(model_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"message": message, "occurred_at": datetime.now(timezone.utc).isoformat()}))


def read_background_error(model_path: str) -> dict | None:
    target = _error_path(model_path)
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text())
    except (OSError, json.JSONDecodeError):
        logger.warning("Failed to read background error %s", target, exc_info=True)
        return None


def clear_background_error(model_path: str) -> None:
    _error_path(model_path).unlink(missing_ok=True)
