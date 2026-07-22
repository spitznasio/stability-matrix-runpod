import asyncio
import sys

from starlette.concurrency import run_in_threadpool

from . import diskio, health, history
from .gpu import get_gpu_telemetry
from .network import get_network_telemetry
from .system import get_system_telemetry
from ..severity import compute_health
from ..supervisor import service_manager

PRODUCER_INTERVAL_S = 2

_subscribers: set[asyncio.Queue] = set()


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=4)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def _publish(payload: dict) -> None:
    for q in list(_subscribers):
        if q.full():
            # A stalled/slow client shouldn't grow memory unbounded — drop
            # its oldest buffered tick rather than blocking the producer.
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        q.put_nowait(payload)


def _service_snapshot(statuses: dict) -> dict:
    latest_health = health.latest()
    return {
        key: {
            "running": status.running,
            "pid": status.pid,
            "uptime_s": status.uptime_s,
            "crashed": status.crashed,
            "health": latest_health.get(key),
        }
        for key, status in statuses.items()
    }


async def build_payload() -> dict:
    system = await run_in_threadpool(get_system_telemetry)
    network = await run_in_threadpool(get_network_telemetry)
    diskio_data = await run_in_threadpool(diskio.get_diskio_telemetry)
    statuses = await run_in_threadpool(service_manager.all_statuses)
    pid_to_service = {status.pid: key for key, status in statuses.items() if status.pid is not None}
    gpu = await run_in_threadpool(get_gpu_telemetry, pid_to_service)

    history.record(system, network, diskio_data, gpu)

    return {
        "system": system,
        "network": network,
        "diskio": diskio_data,
        "gpus": gpu["gpus"] if gpu["available"] else [],
        "services": _service_snapshot(statuses),
        "health": compute_health(system, gpu),
    }


async def producer_loop() -> None:
    while True:
        try:
            payload = await build_payload()
            _publish({"type": "tick", "data": payload})
        except Exception as exc:
            print(f"[server-admin] telemetry producer error: {exc}", file=sys.stderr)
        await asyncio.sleep(PRODUCER_INTERVAL_S)
