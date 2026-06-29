from __future__ import annotations

from pathlib import Path

from msal import SerializableTokenCache

CACHE_DIR = Path("/workspace/onedrive_sync_manager")
CACHE_PATH = CACHE_DIR / "msal_cache.json"


def load_cache() -> SerializableTokenCache:
    cache = SerializableTokenCache()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if CACHE_PATH.exists():
        cache.deserialize(CACHE_PATH.read_text(encoding="utf-8"))
    return cache


def persist_cache(cache: SerializableTokenCache) -> None:
    if not cache.has_state_changed:
        return
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(cache.serialize(), encoding="utf-8")


def clear_cache() -> None:
    if CACHE_PATH.exists():
        CACHE_PATH.unlink()
