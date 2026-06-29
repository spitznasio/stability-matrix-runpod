from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

STATE_DIR = Path("/workspace/onedrive_sync_manager")
MANIFEST_PATH = STATE_DIR / "manifest.json"
_LOCK = Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_manifest() -> dict[str, dict[str, Any]]:
    with _LOCK:
        if not MANIFEST_PATH.exists():
            return {}
        try:
            data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
            return {}
        except json.JSONDecodeError:
            return {}


def save_manifest(manifest: dict[str, dict[str, Any]]) -> None:
    with _LOCK:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def set_manifest_entry(
    manifest: dict[str, dict[str, Any]],
    remote_path: str,
    local_signature: str,
    size: int,
) -> None:
    manifest[remote_path] = {
        "local_signature": local_signature,
        "size": size,
        "synced_at": _now_iso(),
    }
