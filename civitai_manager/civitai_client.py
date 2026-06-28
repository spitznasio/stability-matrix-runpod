import httpx

from . import config


class CivitAIClient:
    def __init__(self, base_url: str = config.CIVITAI_BASE_URL, timeout: float = 15.0):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)

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
    ) -> dict:
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

        response = await self._client.get("/models", params=params)
        response.raise_for_status()
        return response.json()

    async def get_model(self, model_id: int) -> dict:
        response = await self._client.get(f"/models/{model_id}")
        response.raise_for_status()
        return response.json()

    async def get_version_images(self, model_version_id: int, limit: int = 8) -> list[dict]:
        # The model/model-version endpoints never include generation metadata.
        # /images with withMeta=true is the only endpoint that returns it.
        response = await self._client.get(
            "/images",
            params={"modelVersionId": model_version_id, "limit": limit, "withMeta": "true"},
        )
        response.raise_for_status()
        return response.json().get("items", [])
