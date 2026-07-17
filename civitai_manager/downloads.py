import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SIDECAR_SUFFIX = ".civitai.json"


def sidecar_path(file_path: Path) -> Path:
    return file_path.with_name(file_path.name + SIDECAR_SUFFIX)


def write_sidecar(file_path: Path, metadata: dict) -> None:
    sidecar_path(file_path).write_text(json.dumps(metadata, indent=2))


def read_sidecar(file_path: Path) -> dict | None:
    path = sidecar_path(file_path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        logger.warning("Failed to read sidecar %s", path, exc_info=True)
        return None


def list_downloaded_files(download_dir: Path) -> list[dict]:
    if not download_dir.is_dir():
        return []
    files = []
    for path in sorted(download_dir.iterdir()):
        if not path.is_file():
            continue
        if path.name.endswith(".aria2") or path.name.endswith(SIDECAR_SUFFIX):
            continue
        if path.name.startswith("."):
            continue
        if path.with_name(path.name + ".aria2").exists():
            # aria2 keeps a `<name>.aria2` control file next to a download
            # until it completes — its presence means the file is still
            # partial/in-progress, not ready to install.
            continue
        files.append({
            "path": path,
            "name": path.name,
            "size": path.stat().st_size,
            "metadata": read_sidecar(path),
        })
    return files
