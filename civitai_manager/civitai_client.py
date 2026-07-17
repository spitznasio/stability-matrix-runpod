import logging

import httpx

from . import config
from .cache import AsyncTTLCache
from .sanitize import sanitize_html

logger = logging.getLogger(__name__)


class CivitAIClient:
    def __init__(self, base_url: str = config.CIVITAI_BASE_URL, timeout: float = 15.0):
        headers = {}
        if config.CIVITAI_API_TOKEN:
            headers["Authorization"] = f"Bearer {config.CIVITAI_API_TOKEN}"
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout, headers=headers)

        self._search_cache = AsyncTTLCache(
            maxsize=config.CIVITAI_CACHE_MAXSIZE, ttl=config.CIVITAI_CACHE_TTL_SECONDS
        )
        self._model_cache = AsyncTTLCache(
            maxsize=config.CIVITAI_CACHE_MAXSIZE, ttl=config.CIVITAI_CACHE_TTL_SECONDS
        )
        self._images_cache = AsyncTTLCache(
            maxsize=config.CIVITAI_CACHE_MAXSIZE, ttl=config.CIVITAI_CACHE_TTL_SECONDS
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search_models(
        self,
        query: str = "",
        types: list[str] | None = None,
        base_models: list[str] | None = None,
        sort: str = "Most Downloaded",
        period: str = "AllTime",
        nsfw: bool | None = None,
        cursor: str = "",
        limit: int = 20,
        refresh: bool = False,
    ) -> dict:
        key = (
            query,
            tuple(types or ()),
            tuple(base_models or ()),
            sort,
            period,
            nsfw,
            cursor,
            limit,
        )

        async def fetch() -> dict:
            # CivitAI rejects `page` combined with a text `query` ("Cannot use page
            # param with query search. Use cursor-based pagination.") — cursor
            # pagination works for both cases, so it's used unconditionally here.
            # Note: the singular `baseModel` param is silently ignored by the API —
            # only the plural `baseModels` actually filters (confirmed empirically).
            params: dict[str, object] = {
                "limit": limit,
                "sort": sort,
                "period": period,
            }
            if query:
                params["query"] = query
            if types:
                params["types"] = types
            if base_models:
                params["baseModels"] = base_models
            if nsfw is not None:
                params["nsfw"] = str(nsfw).lower()
            if cursor:
                params["cursor"] = cursor

            logger.debug("CivitAI search_models cache miss, fetching: %s", params)
            response = await self._client.get("/models", params=params)
            response.raise_for_status()
            return response.json()

        return await self._search_cache.get_or_fetch(key, fetch, refresh=refresh)

    async def get_model(self, model_id: int, refresh: bool = False) -> dict:
        async def fetch() -> dict:
            logger.debug("CivitAI get_model cache miss, fetching model_id=%s", model_id)
            response = await self._client.get(f"/models/{model_id}")
            response.raise_for_status()
            data = response.json()
            # model/version descriptions are creator-authored HTML rendered
            # with `| safe` in the templates — sanitized once here so the
            # cached result (and every template that touches it) is safe by
            # construction, rather than relying on callers to remember.
            data["description"] = sanitize_html(data.get("description"))
            for version in data.get("modelVersions", []):
                version["description"] = sanitize_html(version.get("description"))
            return data

        return await self._model_cache.get_or_fetch(model_id, fetch, refresh=refresh)

    async def get_version_images(self, model_version_id: int, refresh: bool = False) -> list[dict]:
        # `/images?modelVersionId=` (the only endpoint that returns generation
        # metadata via withMeta=true) pulls from CivitAI's community-wide gallery
        # — every post anyone has ever tagged with this version, not just the
        # publisher's own showcase. `/model-versions/{id}` returns exactly the
        # publisher's curated showcase images, WITH generation metadata included
        # (confirmed empirically — the metadata restriction on /models and
        # /model-versions only applies to the plural /models list endpoint).
        key = model_version_id

        async def fetch() -> list[dict]:
            logger.debug("CivitAI get_version_images cache miss, fetching model_version_id=%s", model_version_id)
            response = await self._client.get(f"/model-versions/{model_version_id}")
            response.raise_for_status()
            return response.json().get("images", [])

        return await self._images_cache.get_or_fetch(key, fetch, refresh=refresh)
