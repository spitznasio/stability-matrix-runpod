from __future__ import annotations

from typing import Any

from msal import PublicClientApplication

from . import config
from .token_store import clear_cache, load_cache, persist_cache


def _build_app() -> tuple[PublicClientApplication, Any]:
    cache = load_cache()
    app = PublicClientApplication(
        client_id=config.ONEDRIVE_CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{config.ONEDRIVE_TENANT_ID}",
        token_cache=cache,
    )
    return app, cache


def get_authorization_flow() -> dict[str, Any]:
    app, _cache = _build_app()
    return app.initiate_auth_code_flow(
        scopes=config.ONEDRIVE_SCOPES,
        redirect_uri=config.ONEDRIVE_REDIRECT_URI,
    )


def complete_authorization_flow(flow: dict[str, Any], auth_response: dict[str, Any]) -> dict[str, Any]:
    app, cache = _build_app()
    result = app.acquire_token_by_auth_code_flow(flow, auth_response)
    persist_cache(cache)
    return result


def acquire_access_token_silent() -> dict[str, Any] | None:
    app, cache = _build_app()
    accounts = app.get_accounts()
    if not accounts:
        return None
    result = app.acquire_token_silent(config.ONEDRIVE_SCOPES, account=accounts[0])
    persist_cache(cache)
    return result


def disconnect() -> None:
    clear_cache()
