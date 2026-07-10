from __future__ import annotations

import logging
from typing import Any

from msal import PublicClientApplication

from . import config
from .token_store import clear_cache, load_cache, persist_cache

logger = logging.getLogger(__name__)


def _build_app() -> tuple[PublicClientApplication, Any]:
    cache = load_cache()
    app = PublicClientApplication(
        client_id=config.ONEDRIVE_CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{config.ONEDRIVE_TENANT_ID}",
        token_cache=cache,
    )
    return app, cache


def get_device_flow() -> dict[str, Any]:
    app, _cache = _build_app()
    return app.initiate_device_flow(scopes=config.ONEDRIVE_SCOPES)


def complete_device_flow(flow: dict[str, Any]) -> dict[str, Any]:
    app, cache = _build_app()
    result = app.acquire_token_by_device_flow(flow)
    persist_cache(cache)
    return result


def acquire_access_token_silent() -> dict[str, Any] | None:
    app, cache = _build_app()
    accounts = app.get_accounts()
    if not accounts:
        return None
    result = app.acquire_token_silent(config.ONEDRIVE_SCOPES, account=accounts[0])
    persist_cache(cache)
    if result is None:
        logger.debug("Silent token acquisition returned no result (re-auth required)")
    return result


def disconnect() -> None:
    logger.info("Clearing MSAL token cache")
    clear_cache()
