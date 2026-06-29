import httpx

from . import config


class InvokeAIClient:
    def __init__(self, base_url: str = config.INVOKEAI_BASE_URL, timeout: float = 15.0):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def install_model(self, download_url: str, access_token: str | None) -> dict:
        params: dict[str, str] = {"source": download_url}
        if access_token:
            params["access_token"] = access_token

        response = await self._client.post("/api/v2/models/install", params=params, json={})
        response.raise_for_status()
        return response.json()

    async def get_install_job(self, job_id: str) -> dict:
        response = await self._client.get(f"/api/v2/models/install/{job_id}")
        response.raise_for_status()
        return response.json()

    async def list_models(self) -> list[dict]:
        response = await self._client.get("/api/v2/models/")
        response.raise_for_status()
        data = response.json()
        return data.get("models", data) if isinstance(data, dict) else data
