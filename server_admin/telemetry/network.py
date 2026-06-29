import time

import psutil

# Process-global last-sample state — correct for a single uvicorn worker.
# Do not run this app with --workers > 1, or each worker computes its own
# (wrong) delta against its own first poll.
_last_sample: dict | None = None


def get_network_telemetry() -> dict:
    global _last_sample

    counters = psutil.net_io_counters()
    now = time.monotonic()
    sample = {"t": now, "bytes_sent": counters.bytes_sent, "bytes_recv": counters.bytes_recv}

    if _last_sample is None or now <= _last_sample["t"]:
        send_rate = recv_rate = 0.0
    else:
        dt = now - _last_sample["t"]
        send_rate = max(0.0, (sample["bytes_sent"] - _last_sample["bytes_sent"]) / dt)
        recv_rate = max(0.0, (sample["bytes_recv"] - _last_sample["bytes_recv"]) / dt)

    _last_sample = sample
    return {
        "bytes_sent_total": counters.bytes_sent,
        "bytes_recv_total": counters.bytes_recv,
        "send_rate_bps": send_rate,
        "recv_rate_bps": recv_rate,
    }
