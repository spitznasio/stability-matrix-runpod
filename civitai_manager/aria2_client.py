import itertools
import logging
import re
from pathlib import Path, PurePosixPath

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
        resolved_url = await self._resolve_download_url(url) if config.CIVITAI_API_TOKEN else url
        if sha256:
            options["checksum"] = f"sha-256={sha256}"
        logger.debug("aria2.addUri out=%s checksum=%s", options["out"], bool(sha256))
        return await self._call("aria2.addUri", [[resolved_url], options])

    async def _resolve_download_url(self, url: str) -> str:
        # CivitAI's download endpoint 307-redirects to a presigned, short-lived
        # Cloudflare R2 URL. aria2 forwards any custom header (e.g. our
        # Authorization header) across that redirect too, and R2 rejects the
        # request with 400 because of the extra header on top of its own
        # query-string signature. So resolve the redirect ourselves with the
        # auth header on this one hop, and hand aria2 only the bare presigned
        # URL — no header needed, and the long-lived API token never reaches
        # aria2 (which may persist download URLs in its session/log files).
        headers = {"Authorization": f"Bearer {config.CIVITAI_API_TOKEN}"}
        async with self._client.stream("GET", url, headers=headers, follow_redirects=False) as response:
            location = response.headers.get("location")
            if 300 <= response.status_code < 400 and location:
                return location
            response.raise_for_status()
            return str(response.url)

    async def tell_status(self, gid: str) -> dict:
        keys = ["gid", "status", "completedLength", "totalLength", "downloadSpeed", "errorCode", "errorMessage", "files"]
        return await self._call("aria2.tellStatus", [gid, keys])

    async def remove(self, gid: str) -> None:
        await self._call("aria2.forceRemove", [gid])

    async def cleanup_control_file(self, gid: str) -> None:
        try:
            job = await self.tell_status(gid)
            files = job.get("files", [])
            for file_info in files:
                path = file_info.get("path")
                if path:
                    control_file = Path(path + ".aria2")
                    if control_file.exists():
                        control_file.unlink()
                        logger.debug("Removed aria2 control file: %s", control_file)
        except Exception as e:
            logger.warning("Failed to cleanup aria2 control files for gid %s: %s", gid, e)
