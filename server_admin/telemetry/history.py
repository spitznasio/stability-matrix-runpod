import time
from collections import deque

# Process-global ring buffers — correct for a single uvicorn worker only, same
# assumption already documented in network.py (do not run with --workers > 1,
# or each worker samples independently into its own, incomplete buffers).

RAW_MAXLEN = 240        # producer ticks every 2s -> 240 * 2s = 8 min
TIER1_BUCKET_S = 30
TIER1_MAXLEN = 360      # 360 * 30s = 3 h
TIER2_BUCKET_S = 300
TIER2_MAXLEN = 288      # 288 * 5min = 24 h


class TieredSeries:
    """RRD-style rolling series: raw points plus two coarser, mean-aggregated
    tiers, each bounded to a fixed point count regardless of process uptime.
    Memory bound for ~20 tracked series (system/network/diskio/per-GPU/
    per-service-VRAM) is sub-megabyte: (240+360+288) points * ~20 series *
    ~56 bytes per (float, float) tuple ~= 1 MB worst case."""

    def __init__(self) -> None:
        self.raw: deque[tuple[float, float]] = deque(maxlen=RAW_MAXLEN)
        self.tier1: deque[tuple[float, float]] = deque(maxlen=TIER1_MAXLEN)
        self.tier2: deque[tuple[float, float]] = deque(maxlen=TIER2_MAXLEN)
        self._t1_bucket_start: float | None = None
        self._t1_acc: list[float] = []
        self._t2_bucket_start: float | None = None
        self._t2_acc: list[float] = []

    def append(self, ts: float, value: float) -> None:
        self.raw.append((ts, value))
        self._roll(ts, value, TIER1_BUCKET_S, "_t1_acc", "_t1_bucket_start", self.tier1)
        self._roll(ts, value, TIER2_BUCKET_S, "_t2_acc", "_t2_bucket_start", self.tier2)

    def _roll(self, ts: float, value: float, bucket_s: float, acc_attr: str, start_attr: str, out: deque) -> None:
        start = getattr(self, start_attr)
        acc = getattr(self, acc_attr)
        if start is None:
            setattr(self, start_attr, ts)
            acc.append(value)
            return
        if ts - start >= bucket_s:
            out.append((start + bucket_s / 2, sum(acc) / len(acc)))
            setattr(self, start_attr, ts)
            acc.clear()
        acc.append(value)

    def snapshot(self) -> dict[str, list[tuple[float, float]]]:
        return {"raw": list(self.raw), "tier1": list(self.tier1), "tier2": list(self.tier2)}


_series: dict[str, TieredSeries] = {
    "cpu_percent": TieredSeries(),
    "mem_percent": TieredSeries(),
    "disk_percent": TieredSeries(),
    "net_send_bps": TieredSeries(),
    "net_recv_bps": TieredSeries(),
    "disk_read_bps": TieredSeries(),
    "disk_write_bps": TieredSeries(),
}
_gpu_series: dict[int, TieredSeries] = {}
_service_vram_series: dict[str, TieredSeries] = {}


def get_history(metric: str, tier: str = "raw") -> list[tuple[float, float]]:
    series = _series.get(metric)
    return series.snapshot()[tier] if series else []


def get_gpu_history(index: int, tier: str = "raw") -> list[tuple[float, float]]:
    series = _gpu_series.get(index)
    return series.snapshot()[tier] if series else []


def get_service_vram_history(service_key: str, tier: str = "raw") -> list[tuple[float, float]]:
    series = _service_vram_series.get(service_key)
    return series.snapshot()[tier] if series else []


def record(system: dict, network: dict, diskio: dict, gpu: dict) -> None:
    now = time.time()
    _series["cpu_percent"].append(now, system["cpu_percent"])
    _series["mem_percent"].append(now, system["mem_percent"])
    _series["disk_percent"].append(now, system["disk_percent"])
    _series["net_send_bps"].append(now, network["send_rate_bps"])
    _series["net_recv_bps"].append(now, network["recv_rate_bps"])
    _series["disk_read_bps"].append(now, diskio["read_rate_bps"])
    _series["disk_write_bps"].append(now, diskio["write_rate_bps"])

    # Aggregated by service_key rather than PID, since PIDs churn on service
    # restart — a continuous per-service VRAM trend needs a stable key.
    service_vram_mb: dict[str, float] = {}
    if gpu["available"]:
        for g in gpu["gpus"]:
            _gpu_series.setdefault(g["index"], TieredSeries()).append(now, g["utilization_gpu"])
            for proc in g.get("processes", []):
                key = proc.get("service_key")
                used = proc.get("used_memory_mb")
                if key and used is not None:
                    service_vram_mb[key] = service_vram_mb.get(key, 0.0) + used
    for key, mb in service_vram_mb.items():
        _service_vram_series.setdefault(key, TieredSeries()).append(now, mb)


def get_full_snapshot() -> dict:
    return {
        "series": {name: s.snapshot() for name, s in _series.items()},
        "gpu": {index: s.snapshot() for index, s in _gpu_series.items()},
        "service_vram": {key: s.snapshot() for key, s in _service_vram_series.items()},
    }
