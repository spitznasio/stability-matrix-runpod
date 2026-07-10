import itertools
import logging
import re
from pathlib import PurePosixPath

import httpx

from . import config

logger = logging.getLogger(__name__)

# aria2's own status vocabulary (see aria2.tellStatus docs) — used by main.py
# to decide when to stop polling a download.
TERMINAL_STATUSES = {"complete", "error", "removed"}


def _sanitize_filename(name: str) -> str:
    # `name` comes from CivitAI's file metadata (untrusted upstream data) and
    # is passed to aria2 as the literal output filename — reduce to a bare
    # basename so it can't escape CIVITAI_DOWNLOAD_DIR via path separators.
    base = PurePosixPath(name).name
    base = re.sub(r"[^\w.\- ]", "_", base)
    return base or "download"


class Aria2Client:
    def __init__(self, rpc_url: str = config.ARIA2_RPC_URL, secret: str = config.ARIA2_RPC_SECRET, timeout: float = 15.0):
        self._client = httpx.AsyncClient(timeout=timeout)
        self._rpc_url = rpc_url
        self._secret = secret
        self._ids = itertools.count(1)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _call(self, method: str, params: list) -> dict:
        request_id = next(self._ids)
        payload = {
            "jsonrpc": "2.0",
            "id": str(request_id),
            "method": method,
            "params": [f"token:{self._secret}", *params],
        }
        response = await self._client.post(self._rpc_url, json=payload)
        response.raise_for_status()
        body = response.json()
        if "error" in body:
            logger.warning("aria2 RPC error calling %s: %s", method, body["error"])
            raise httpx.HTTPError(f"aria2 RPC error calling {method}: {body['error']}")
        return body["result"]

    async def add_download(self, url: str, filename: str, sha256: str | None = None) -> str:
        options: dict[str, object] = {
            "dir": config.CIVITAI_DOWNLOAD_DIR,
            "out": _sanitize_filename(filename),
            "continue": "true",
        }
        if config.CIVITAI_API_TOKEN:
            options["header"] = [f"Authorization: Bearer {config.CIVITAI_API_TOKEN}"]
        if sha256:
            options["checksum"] = f"sha-256={sha256}"
        logger.debug("aria2.addUri out=%s checksum=%s", options["out"], bool(sha256))
        return await self._call("aria2.addUri", [[url], options])

    async def tell_status(self, gid: str) -> dict:
        keys = ["gid", "status", "completedLength", "totalLength", "downloadSpeed", "errorCode", "errorMessage", "files"]
        return await self._call("aria2.tellStatus", [gid, keys])

    async def remove(self, gid: str) -> None:
        await self._call("aria2.forceRemove", [gid])
