import hashlib
import json
import logging
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
