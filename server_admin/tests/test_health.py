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
