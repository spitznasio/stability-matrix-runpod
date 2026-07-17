import asyncio
import time
from collections import deque

from starlette.concurrency import run_in_threadpool

from .gpu import get_gpu_telemetry
from .network import get_network_telemetry
from .system import get_system_telemetry

# Process-global ring buffers — correct for a single uvicorn worker only, same
# assumption already documented in network.py (do not run with --workers > 1,
# or each worker samples independently into its own, incomplete buffers).
SAMPLE_INTERVAL_S = 20
WINDOW_S = 45 * 60
_MAXLEN = WINDOW_S // SAMPLE_INTERVAL_S

_history: dict[str, deque] = {
    "cpu_percent": deque(maxlen=_MAXLEN),
    "mem_percent": deque(maxlen=_MAXLEN),
    "net_send_bps": deque(maxlen=_MAXLEN),
    "net_recv_bps": deque(maxlen=_MAXLEN),
}
_gpu_history: dict[int, deque] = {}


def get_history(metric: str) -> list[tuple[float, float]]:
    return list(_history.get(metric, ()))


def get_gpu_history(index: int) -> list[tuple[float, float]]:
    buf = _gpu_history.get(index)
    return list(buf) if buf is not None else []


async def _sample_once() -> None:
    now = time.time()

    system = await run_in_threadpool(get_system_telemetry)
    _history["cpu_percent"].append((now, system["cpu_percent"]))
    _history["mem_percent"].append((now, system["mem_percent"]))

    network = await run_in_threadpool(get_network_telemetry)
    _history["net_send_bps"].append((now, network["send_rate_bps"]))
    _history["net_recv_bps"].append((now, network["recv_rate_bps"]))

    gpu = await run_in_threadpool(get_gpu_telemetry)
    if gpu["available"]:
        for g in gpu["gpus"]:
            buf = _gpu_history.setdefault(g["index"], deque(maxlen=_MAXLEN))
            buf.append((now, g["utilization_gpu"]))


async def sample_loop() -> None:
    while True:
        try:
            await _sample_once()
        except Exception:
            # A single failed sample (e.g. transient nvidia-smi hiccup)
            # shouldn't kill the background task for the rest of the process.
            pass
        await asyncio.sleep(SAMPLE_INTERVAL_S)
