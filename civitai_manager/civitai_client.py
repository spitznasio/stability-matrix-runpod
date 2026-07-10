import logging

import httpx

from . import config
from .cache import AsyncTTLCache

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
            return response.json()

        return await self._model_cache.get_or_fetch(model_id, fetch, refresh=refresh)

    # Bitmask covering every CivitAI browsing level (None|Soft|Mature|X|Blocked
    # = 1|2|4|8|16). The legacy `nsfw` param is an exclusive switch (omitted =
    # safe only, `nsfw=true` = NSFW only, never both), so `browsingLevel` is
    # used instead to match /browse's "NSFW included by default" behavior.
    ALL_BROWSING_LEVELS = 31

    async def get_version_images(
        self, model_version_id: int, limit: int = 24, refresh: bool = False
    ) -> list[dict]:
        key = (model_version_id, limit)

        async def fetch() -> list[dict]:
            # The model/model-version endpoints never include generation metadata.
            # /images with withMeta=true is the only endpoint that returns it.
            logger.debug("CivitAI get_version_images cache miss, fetching model_version_id=%s", model_version_id)
            response = await self._client.get(
                "/images",
                params={
                    "modelVersionId": model_version_id,
                    "limit": limit,
                    "withMeta": "true",
                    "browsingLevel": self.ALL_BROWSING_LEVELS,
                },
            )
            response.raise_for_status()
            return response.json().get("items", [])

        return await self._images_cache.get_or_fetch(key, fetch, refresh=refresh)
