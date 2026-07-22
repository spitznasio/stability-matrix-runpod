from unittest.mock import AsyncMock

import httpx
import pytest

from server_admin.telemetry import health


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


@pytest.mark.asyncio
async def test_check_once_success_marks_up():
    client = AsyncMock()
    client.get = AsyncMock(return_value=FakeResponse(200))

    await health._check_once(client, "invokeai", "http://x", 2.0)

    result = health.latest()["invokeai"]
    assert result["up"] is True
    assert result["status_code"] == 200
    assert result["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_check_once_5xx_marks_down():
    client = AsyncMock()
    client.get = AsyncMock(return_value=FakeResponse(503))

    await health._check_once(client, "invokeai", "http://x", 2.0)

    assert health.latest()["invokeai"]["up"] is False
    assert health.latest()["invokeai"]["status_code"] == 503


@pytest.mark.asyncio
async def test_check_once_timeout_marks_down_with_no_status():
    client = AsyncMock()
    client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

    await health._check_once(client, "code-server", "http://x", 2.0)

    result = health.latest()["code-server"]
    assert result["up"] is False
    assert result["status_code"] is None
    assert result["latency_ms"] is None


@pytest.mark.asyncio
async def test_check_once_connect_error_marks_down():
    client = AsyncMock()
    client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

    await health._check_once(client, "civitai-manager", "http://x", 2.0)

    assert health.latest()["civitai-manager"]["up"] is False


@pytest.mark.asyncio
async def test_check_once_read_error_marks_down():
    """Verify that other HTTPError subclasses (beyond TimeoutException/ConnectError)
    are caught and handled the same way: mark as down with no status/latency."""
    client = AsyncMock()
    client.get = AsyncMock(side_effect=httpx.ReadError("connection reset"))

    await health._check_once(client, "invokeai", "http://x", 2.0)

    result = health.latest()["invokeai"]
    assert result["up"] is False
    assert result["status_code"] is None
    assert result["latency_ms"] is None


def test_latest_returns_deep_copy():
    """Verify that latest() returns a deep-enough copy so callers cannot
    mutate the internal cache by modifying returned values."""
    # Set up a known state in the cache
    health._latest["test-service"] = {"up": True, "status_code": 200, "latency_ms": 10.0}

    # Get a copy and mutate it
    result = health.latest()
    result["test-service"]["up"] = False
    result["test-service"]["status_code"] = 500

    # Verify the internal cache is unaffected
    cached = health._latest["test-service"]
    assert cached["up"] is True
    assert cached["status_code"] == 200
