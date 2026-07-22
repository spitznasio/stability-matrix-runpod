import time

import psutil

# Process-global last-sample state — correct for a single uvicorn worker only,
# same assumption already documented in network.py. Do not run this app with
# --workers > 1, or each worker computes its own (wrong) delta.
_last_sample: dict | None = None


def get_diskio_telemetry() -> dict:
    """Host-wide disk I/O rates (not cgroup-scoped — there's no cgroup io
    controller wired up elsewhere in this codebase, unlike the cpu/mem
    accounting in system.py, so this is a known limitation rather than
    something fixable here)."""
    global _last_sample

    counters = psutil.disk_io_counters()
    if counters is None:
        # psutil documents this as possible in some container/sandboxed
        # environments without block-device access.
        return {
            "read_bytes_total": None,
            "write_bytes_total": None,
            "read_rate_bps": 0.0,
            "write_rate_bps": 0.0,
        }

    now = time.monotonic()
    sample = {"t": now, "read_bytes": counters.read_bytes, "write_bytes": counters.write_bytes}

    if _last_sample is None or now <= _last_sample["t"]:
        read_rate = write_rate = 0.0
    else:
        dt = now - _last_sample["t"]
        read_rate = max(0.0, (sample["read_bytes"] - _last_sample["read_bytes"]) / dt)
        write_rate = max(0.0, (sample["write_bytes"] - _last_sample["write_bytes"]) / dt)

    _last_sample = sample
    return {
        "read_bytes_total": counters.read_bytes,
        "write_bytes_total": counters.write_bytes,
        "read_rate_bps": read_rate,
        "write_rate_bps": write_rate,
    }
