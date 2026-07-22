import asyncio
import time
from unittest.mock import patch

import pytest

from server_admin.telemetry import broadcast


def test_subscribe_adds_and_unsubscribe_removes():
    q = broadcast.subscribe()
    assert q in broadcast._subscribers
    broadcast.unsubscribe(q)
    assert q not in broadcast._subscribers


def test_publish_drops_oldest_when_queue_full():
    q = asyncio.Queue(maxsize=2)
    broadcast._subscribers.add(q)
    try:
        broadcast._publish({"type": "tick", "data": 1})
        broadcast._publish({"type": "tick", "data": 2})
        broadcast._publish({"type": "tick", "data": 3})

        assert q.qsize() == 2
        assert q.get_nowait()["data"] == 2
        assert q.get_nowait()["data"] == 3
    finally:
        broadcast._subscribers.discard(q)


@pytest.mark.asyncio
async def test_build_payload_assembles_and_records_history():
    fake_system = {"cpu_percent": 10.0, "mem_percent": 20.0, "disk_percent": 30.0}
    fake_network = {"send_rate_bps": 1.0, "recv_rate_bps": 2.0}
    fake_diskio = {"read_rate_bps": 3.0, "write_rate_bps": 4.0}
    fake_gpu = {"available": False, "reason": "no gpu", "gpus": []}
    fake_statuses = {}

    with patch.object(broadcast, "get_system_telemetry", return_value=fake_system), \
         patch.object(broadcast, "get_network_telemetry", return_value=fake_network), \
         patch.object(broadcast.diskio, "get_diskio_telemetry", return_value=fake_diskio), \
         patch.object(broadcast.service_manager, "all_statuses", return_value=fake_statuses), \
         patch.object(broadcast, "get_gpu_telemetry", return_value=fake_gpu), \
         patch.object(broadcast.history, "record") as mock_record:
        payload = await broadcast.build_payload()

    mock_record.assert_called_once_with(fake_system, fake_network, fake_diskio, fake_gpu)
    assert payload["system"] == fake_system
    assert payload["network"] == fake_network
    assert payload["diskio"] == fake_diskio
    assert payload["gpus"] == []
    assert payload["services"] == {}
    assert payload["health"] == "ok"
    assert isinstance(payload["ts"], float)
    assert abs(payload["ts"] - time.time()) < 5


@pytest.mark.asyncio
async def test_build_payload_includes_service_health():
    fake_statuses = {
        "invokeai": type("S", (), {"running": True, "pid": 123, "uptime_s": 42.0, "crashed": False})(),
    }
    fake_system = {"cpu_percent": 1.0, "mem_percent": 1.0, "disk_percent": 1.0}
    fake_gpu = {"available": False, "reason": None, "gpus": []}

    with patch.object(broadcast, "get_system_telemetry", return_value=fake_system), \
         patch.object(broadcast, "get_network_telemetry", return_value={"send_rate_bps": 0.0, "recv_rate_bps": 0.0}), \
         patch.object(broadcast.diskio, "get_diskio_telemetry", return_value={"read_rate_bps": 0.0, "write_rate_bps": 0.0}), \
         patch.object(broadcast.service_manager, "all_statuses", return_value=fake_statuses), \
         patch.object(broadcast, "get_gpu_telemetry", return_value=fake_gpu), \
         patch.object(broadcast.history, "record"), \
         patch.object(broadcast.health, "latest", return_value={"invokeai": {"up": True, "status_code": 200, "latency_ms": 5.0}}):
        payload = await broadcast.build_payload()

    assert payload["services"]["invokeai"] == {
        "running": True,
        "pid": 123,
        "uptime_s": 42.0,
        "crashed": False,
        "health": {"up": True, "status_code": 200, "latency_ms": 5.0},
    }
