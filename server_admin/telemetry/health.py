import asyncio
import sys
import time

import httpx

# code-server has no JSON health endpoint — any non-5xx, non-connection-error
# response means "up". civitai-manager's /health is confirmed at
# civitai_manager/main.py:717. InvokeAI's own lightweight health path is a
# third-party detail (not in this repo); "/" is used here as the safest
# universally-present endpoint rather than assuming a /health path exists.
HEALTH_TARGETS = {
    "invokeai": ("http://127.0.0.1:9090/", 2.0),
    "code-server": ("http://127.0.0.1:8080/", 2.0),
    "civitai-manager": ("http://127.0.0.1:8000/health", 2.0),
}
HEALTH_INTERVAL_S = 15

_latest: dict[str, dict] = {}


def latest() -> dict:
    return dict(_latest)


async def _check_once(client: httpx.AsyncClient, key: str, url: str, timeout: float) -> None:
    start = time.monotonic()
    try:
        resp = await client.get(url, timeout=timeout)
        elapsed_ms = (time.monotonic() - start) * 1000
        _latest[key] = {"up": resp.status_code < 500, "status_code": resp.status_code, "latency_ms": elapsed_ms}
    except (httpx.TimeoutException, httpx.ConnectError):
        _latest[key] = {"up": False, "status_code": None, "latency_ms": None}


async def health_loop() -> None:
    """Independent of the 2s telemetry producer cadence — a slow/hanging
    health check must not block telemetry ticks."""
    async with httpx.AsyncClient() as client:
        while True:
            try:
                await asyncio.gather(*(_check_once(client, k, u, t) for k, (u, t) in HEALTH_TARGETS.items()))
            except Exception as exc:
                print(f"[server-admin] health check loop error: {exc}", file=sys.stderr)
            await asyncio.sleep(HEALTH_INTERVAL_S)
