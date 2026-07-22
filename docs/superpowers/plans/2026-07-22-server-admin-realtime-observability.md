# Server Admin Real-Time Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Server Admin dashboard's 3s htmx polling with a Server-Sent-Events push model, add tiered in-memory historical trends, and surface disk I/O + HTTP health-check telemetry, rendered via vendored uPlot charts.

**Architecture:** One background `producer_loop()` (2s cadence) samples all telemetry once and fans it out to any number of SSE subscribers via bounded `asyncio.Queue`s; the same tick feeds a tiered ring-buffer history module. A separate, slower `health_loop()` (15s cadence) independently pings the three HTTP services this dashboard supervises. `/dashboard/gpu` stays on its current htmx poll this pass (its process-table restart buttons are out of scope for the SSE migration).

**Tech Stack:** FastAPI/Starlette (existing), `psutil`/`pynvml`/`httpx` (existing, already installed), uPlot 1.6.32 (new, vendored, zero JS dependencies), pytest + pytest-asyncio (new, dev-only, not shipped in the Docker image).

## Global Constraints

- Single-uvicorn-worker only — telemetry rate-delta calculations (`network.py`, new `diskio.py`) use process-global state that breaks under multiple workers. Do not add `--workers` to any uvicorn invocation.
- No SQLite, no external DB, no persistence beyond what already exists (`/workspace/server-admin/env-overrides.env`). All new history is in-memory and lost on restart — this is accepted, not a bug to fix.
- No alerting/notifications (Discord, email, webhook) in this pass.
- No changes to `Dockerfile`/`Dockerfile.4090` — pytest and friends are dev-only, never installed in the production image.
- `/dashboard/gpu`, `/services`, `/logs`, `/environment` stay on htmx, unchanged, except where explicitly noted.
- Follow existing code conventions: module-level docstrings/comments explaining *why* (see `network.py`'s single-worker comment, `system.py`'s `DISK_PATH` comment) — match that style in new telemetry modules.

---

## Test tooling setup (folded into Task 1)

No `tests/` directory or pytest config exists yet in this repo. Task 1 below creates both, alongside the first real module.

---

### Task 1: Disk I/O telemetry (`telemetry/diskio.py`)

**Files:**
- Create: `server_admin/tests/__init__.py` (empty)
- Create: `server_admin/tests/test_diskio.py`
- Create: `server_admin/telemetry/diskio.py`
- Create: `pytest.ini` (repo root)
- Create: `server_admin/requirements-test.txt`

**Interfaces:**
- Produces: `get_diskio_telemetry() -> dict` with keys `read_bytes_total: int | None`, `write_bytes_total: int | None`, `read_rate_bps: float`, `write_rate_bps: float`. Module-global `_last_sample: dict | None`, reset-able by tests via `diskio._last_sample = None`.

- [ ] **Step 1: Add pytest config and test dependencies**

Create `pytest.ini` at repo root:

```ini
[pytest]
testpaths = server_admin/tests
asyncio_mode = auto
```

Create `server_admin/requirements-test.txt`:

```
pytest==8.3.4
pytest-asyncio==0.24.0
```

Create empty `server_admin/tests/__init__.py`.

- [ ] **Step 2: Install test dependencies into the local venv**

Run: `.venv/bin/pip install -r server_admin/requirements-test.txt`
Expected: `Successfully installed pytest-8.3.4 pytest-asyncio-0.24.0 ...`

- [ ] **Step 3: Write the failing test**

Create `server_admin/tests/test_diskio.py`:

```python
from server_admin.telemetry import diskio


class FakeCounters:
    def __init__(self, read_bytes, write_bytes):
        self.read_bytes = read_bytes
        self.write_bytes = write_bytes


def test_first_sample_reports_zero_rate(monkeypatch):
    diskio._last_sample = None
    monkeypatch.setattr(diskio.psutil, "disk_io_counters", lambda: FakeCounters(1000, 2000))
    monkeypatch.setattr(diskio.time, "monotonic", lambda: 10.0)

    result = diskio.get_diskio_telemetry()

    assert result["read_rate_bps"] == 0.0
    assert result["write_rate_bps"] == 0.0
    assert result["read_bytes_total"] == 1000
    assert result["write_bytes_total"] == 2000


def test_second_sample_computes_rate(monkeypatch):
    diskio._last_sample = None
    counters = iter([FakeCounters(1000, 2000), FakeCounters(3000, 2500)])
    times = iter([10.0, 15.0])
    monkeypatch.setattr(diskio.psutil, "disk_io_counters", lambda: next(counters))
    monkeypatch.setattr(diskio.time, "monotonic", lambda: next(times))

    diskio.get_diskio_telemetry()
    result = diskio.get_diskio_telemetry()

    assert result["read_rate_bps"] == (3000 - 1000) / 5.0
    assert result["write_rate_bps"] == (2500 - 2000) / 5.0


def test_none_counters_returns_zero_without_crashing(monkeypatch):
    diskio._last_sample = None
    monkeypatch.setattr(diskio.psutil, "disk_io_counters", lambda: None)

    result = diskio.get_diskio_telemetry()

    assert result["read_rate_bps"] == 0.0
    assert result["write_rate_bps"] == 0.0
    assert result["read_bytes_total"] is None
    assert result["write_bytes_total"] is None
```

- [ ] **Step 4: Run test to verify it fails**

Run: `.venv/bin/pytest server_admin/tests/test_diskio.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server_admin.telemetry.diskio'`

- [ ] **Step 5: Write the implementation**

Create `server_admin/telemetry/diskio.py`:

```python
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
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/bin/pytest server_admin/tests/test_diskio.py -v`
Expected: `3 passed`

- [ ] **Step 7: Commit**

```bash
git add pytest.ini server_admin/requirements-test.txt server_admin/tests/__init__.py server_admin/tests/test_diskio.py server_admin/telemetry/diskio.py
git commit -m "feat(server-admin): add disk I/O telemetry with tests"
```

---

### Task 2: Extract health-badge severity logic (`severity.py`)

Needed before Task 5 (`broadcast.py`) to avoid a circular import — `broadcast.py` needs `compute_health()`, but that currently lives in `main.py`, and `main.py` will need to import `broadcast`.

**Files:**
- Create: `server_admin/tests/test_severity.py`
- Create: `server_admin/severity.py`
- Modify: `server_admin/main.py:79-102` (remove `_percent_severity`/`compute_health`, import from `severity` instead)

**Interfaces:**
- Produces: `percent_severity(percent: float) -> str` (returns `"ok"`/`"warn"`/`"danger"`), `compute_health(system: dict, gpu: dict) -> str`. Identical behavior to the current `main.py` versions — this is a pure move, not a behavior change.

- [ ] **Step 1: Write the failing test**

Create `server_admin/tests/test_severity.py`:

```python
from server_admin import severity


def test_percent_severity_thresholds():
    assert severity.percent_severity(50.0) == "ok"
    assert severity.percent_severity(70.0) == "warn"
    assert severity.percent_severity(89.9) == "warn"
    assert severity.percent_severity(90.0) == "danger"


def test_compute_health_ok_when_all_low():
    system = {"cpu_percent": 10.0, "mem_percent": 10.0, "disk_percent": 10.0}
    gpu = {"available": False, "gpus": []}
    assert severity.compute_health(system, gpu) == "ok"


def test_compute_health_warn_from_gpu_temperature():
    system = {"cpu_percent": 10.0, "mem_percent": 10.0, "disk_percent": 10.0}
    gpu = {"available": True, "gpus": [{"utilization_gpu": 10.0, "temperature_c": 75.0}]}
    assert severity.compute_health(system, gpu) == "warn"


def test_compute_health_danger_from_system_disk():
    system = {"cpu_percent": 10.0, "mem_percent": 10.0, "disk_percent": 95.0}
    gpu = {"available": False, "gpus": []}
    assert severity.compute_health(system, gpu) == "danger"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest server_admin/tests/test_severity.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server_admin.severity'`

- [ ] **Step 3: Write the implementation**

Create `server_admin/severity.py`:

```python
def percent_severity(percent: float) -> str:
    if percent >= 90:
        return "danger"
    if percent >= 70:
        return "warn"
    return "ok"


def compute_health(system: dict, gpu: dict) -> str:
    severities = [
        percent_severity(system["cpu_percent"]),
        percent_severity(system["mem_percent"]),
        percent_severity(system["disk_percent"]),
    ]
    if gpu["available"]:
        for g in gpu["gpus"]:
            severities.append(percent_severity(g["utilization_gpu"]))
            if g["temperature_c"] is not None:
                severities.append(percent_severity(g["temperature_c"]))
    if "danger" in severities:
        return "danger"
    if "warn" in severities:
        return "warn"
    return "ok"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest server_admin/tests/test_severity.py -v`
Expected: `4 passed`

- [ ] **Step 5: Update `main.py` to use the extracted module**

In `server_admin/main.py`, delete lines 79-102 (the `_percent_severity` and `compute_health` function definitions), and change the import block at the top:

```python
from . import config, env_vars
from .formatting import format_bytes, format_rate, format_uptime, sparkline_points
from .logs import log_file_path, search_log, tail_log
from .severity import compute_health
from .supervisor import SERVICES, monitor_loop, service_manager
from .telemetry import gpu as gpu_telemetry
from .telemetry import history
from .telemetry.gpu import get_gpu_telemetry
from .telemetry.network import get_network_telemetry
from .telemetry.system import get_system_telemetry
```

All existing call sites (`compute_health(system, gpu)` in `status_strip()`) are unchanged — same name, same signature, same behavior.

- [ ] **Step 6: Manually verify `main.py` still imports cleanly**

Run: `.venv/bin/python -c "from server_admin import main"`
Expected: no output, exit code 0 (no `ImportError`/`NameError`)

- [ ] **Step 7: Commit**

```bash
git add server_admin/severity.py server_admin/tests/test_severity.py server_admin/main.py
git commit -m "refactor(server-admin): extract health-severity logic to severity.py"
```

---

### Task 3: Tiered history ring buffers (`telemetry/history.py`)

Replaces the flat 45-minute `deque` per metric with a 3-tier RRD-style structure (raw/2s/8min, tier1/30s/3h, tier2/5min/24h), bounded regardless of process uptime. Also adds `disk_percent`, `disk_read_bps`, `disk_write_bps`, and per-service VRAM series.

**Files:**
- Create: `server_admin/tests/test_history.py`
- Modify: `server_admin/telemetry/history.py` (full rewrite)

**Interfaces:**
- Consumes: nothing new (no more direct calls to `get_system_telemetry`/`get_network_telemetry`/`get_gpu_telemetry` — those move to `broadcast.py` in Task 5)
- Produces:
  - `TieredSeries` class with `.append(ts: float, value: float) -> None` and `.snapshot() -> dict[str, list[tuple[float, float]]]` (keys `"raw"`, `"tier1"`, `"tier2"`)
  - `get_history(metric: str, tier: str = "raw") -> list[tuple[float, float]]`
  - `get_gpu_history(index: int, tier: str = "raw") -> list[tuple[float, float]]` (same signature as today's default-arg call site in `main.py:174`, backward compatible)
  - `get_service_vram_history(service_key: str, tier: str = "raw") -> list[tuple[float, float]]`
  - `record(system: dict, network: dict, diskio: dict, gpu: dict) -> None`
  - `get_full_snapshot() -> dict` with keys `"series"` (dict of metric name → `TieredSeries.snapshot()`), `"gpu"` (dict of GPU index → snapshot), `"service_vram"` (dict of service key → snapshot)

- [ ] **Step 1: Write the failing tests**

Create `server_admin/tests/test_history.py`:

```python
from server_admin.telemetry import history


def test_raw_tier_respects_maxlen():
    series = history.TieredSeries()
    for i in range(250):
        series.append(float(i * 2), float(i))

    snap = series.snapshot()
    assert len(snap["raw"]) == history.RAW_MAXLEN
    # oldest 10 points (i=0..9) should have been evicted
    assert snap["raw"][0] == (20.0, 10.0)


def test_tier1_aggregates_on_bucket_boundary():
    series = history.TieredSeries()
    series.append(0.0, 2.0)
    series.append(10.0, 2.0)
    series.append(20.0, 2.0)
    series.append(35.0, 8.0)  # crosses the 30s boundary, flushes the first bucket

    snap = series.snapshot()
    assert snap["tier1"] == [(15.0, 2.0)]


def test_tier2_respects_maxlen():
    series = history.TieredSeries()
    for i in range(300):
        series.append(float(i * history.TIER2_BUCKET_S), float(i))

    snap = series.snapshot()
    assert len(snap["tier2"]) == history.TIER2_MAXLEN


def test_get_history_and_get_gpu_history_default_to_raw():
    history._series["cpu_percent"] = history.TieredSeries()
    history._series["cpu_percent"].append(1.0, 42.0)
    history._gpu_series[0] = history.TieredSeries()
    history._gpu_series[0].append(1.0, 55.0)

    assert history.get_history("cpu_percent") == [(1.0, 42.0)]
    assert history.get_gpu_history(0) == [(1.0, 55.0)]
    assert history.get_history("unknown_metric") == []
    assert history.get_gpu_history(99) == []


def test_record_appends_to_system_network_diskio_series(monkeypatch):
    monkeypatch.setattr(history.time, "time", lambda: 100.0)
    for series in history._series.values():
        series.raw.clear()

    system = {"cpu_percent": 5.0, "mem_percent": 6.0, "disk_percent": 7.0}
    network = {"send_rate_bps": 1.0, "recv_rate_bps": 2.0}
    diskio = {"read_rate_bps": 3.0, "write_rate_bps": 4.0}
    gpu = {"available": False, "gpus": []}

    history.record(system, network, diskio, gpu)

    assert history.get_history("cpu_percent")[-1] == (100.0, 5.0)
    assert history.get_history("mem_percent")[-1] == (100.0, 6.0)
    assert history.get_history("disk_percent")[-1] == (100.0, 7.0)
    assert history.get_history("net_send_bps")[-1] == (100.0, 1.0)
    assert history.get_history("net_recv_bps")[-1] == (100.0, 2.0)
    assert history.get_history("disk_read_bps")[-1] == (100.0, 3.0)
    assert history.get_history("disk_write_bps")[-1] == (100.0, 4.0)


def test_record_aggregates_per_service_vram(monkeypatch):
    monkeypatch.setattr(history.time, "time", lambda: 200.0)
    history._service_vram_series.clear()

    system = {"cpu_percent": 1.0, "mem_percent": 1.0, "disk_percent": 1.0}
    network = {"send_rate_bps": 0.0, "recv_rate_bps": 0.0}
    diskio = {"read_rate_bps": 0.0, "write_rate_bps": 0.0}
    gpu = {
        "available": True,
        "gpus": [
            {
                "index": 0,
                "utilization_gpu": 50.0,
                "processes": [
                    {"pid": 1, "service_key": "invokeai", "used_memory_mb": 1000.0},
                    {"pid": 2, "service_key": None, "used_memory_mb": 200.0},
                ],
            }
        ],
    }

    history.record(system, network, diskio, gpu)

    assert history.get_service_vram_history("invokeai") == [(200.0, 1000.0)]
    assert history.get_gpu_history(0)[-1] == (200.0, 50.0)


def test_get_full_snapshot_shape():
    snap = history.get_full_snapshot()
    assert set(snap.keys()) == {"series", "gpu", "service_vram"}
    assert "cpu_percent" in snap["series"]
    assert set(snap["series"]["cpu_percent"].keys()) == {"raw", "tier1", "tier2"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest server_admin/tests/test_history.py -v`
Expected: FAIL — `AttributeError: module 'server_admin.telemetry.history' has no attribute 'TieredSeries'` (and similar for other missing names)

- [ ] **Step 3: Write the implementation**

Replace the full contents of `server_admin/telemetry/history.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest server_admin/tests/test_history.py -v`
Expected: `8 passed`

- [ ] **Step 5: Commit**

```bash
git add server_admin/telemetry/history.py server_admin/tests/test_history.py
git commit -m "feat(server-admin): replace flat history buffers with tiered RRD-style series"
```

---

### Task 4: HTTP health-check telemetry (`telemetry/health.py`)

**Files:**
- Create: `server_admin/tests/test_health.py`
- Create: `server_admin/telemetry/health.py`

**Interfaces:**
- Produces: `latest() -> dict[str, dict]` (keys: `"invokeai"`, `"code-server"`, `"civitai-manager"`; each value has `up: bool`, `status_code: int | None`, `latency_ms: float | None`), `health_loop() -> None` (never returns, run as an asyncio task), `_check_once(client, key, url, timeout)` (internal, used directly by tests).

- [ ] **Step 1: Write the failing tests**

Create `server_admin/tests/test_health.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest server_admin/tests/test_health.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server_admin.telemetry.health'`

- [ ] **Step 3: Write the implementation**

Create `server_admin/telemetry/health.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest server_admin/tests/test_health.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add server_admin/telemetry/health.py server_admin/tests/test_health.py
git commit -m "feat(server-admin): add HTTP health/latency checks for supervised web services"
```

---

### Task 5: Producer loop and SSE pub/sub (`telemetry/broadcast.py`)

**Files:**
- Create: `server_admin/tests/test_broadcast.py`
- Create: `server_admin/telemetry/broadcast.py`

**Interfaces:**
- Consumes: `history.record(system, network, diskio, gpu)` (Task 3), `severity.compute_health(system, gpu)` (Task 2), `diskio.get_diskio_telemetry()` (Task 1), `health.latest()` (Task 4), existing `get_system_telemetry`, `get_network_telemetry`, `get_gpu_telemetry`, `service_manager.all_statuses()`
- Produces: `subscribe() -> asyncio.Queue`, `unsubscribe(q: asyncio.Queue) -> None`, `build_payload() -> dict` (keys: `system`, `network`, `diskio`, `gpus`, `services`, `health`), `producer_loop() -> None` (never returns, run as an asyncio task), module-level `_subscribers: set[asyncio.Queue]` (used directly by tests)

- [ ] **Step 1: Write the failing tests**

Create `server_admin/tests/test_broadcast.py`:

```python
import asyncio
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest server_admin/tests/test_broadcast.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server_admin.telemetry.broadcast'`

- [ ] **Step 3: Write the implementation**

Create `server_admin/telemetry/broadcast.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest server_admin/tests/test_broadcast.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add server_admin/telemetry/broadcast.py server_admin/tests/test_broadcast.py
git commit -m "feat(server-admin): add SSE producer loop and pub/sub broadcast"
```

---

### Task 6: SSE route and lifespan wiring in `main.py`

**Files:**
- Create: `server_admin/tests/test_dashboard_stream.py`
- Modify: `server_admin/main.py`

**Interfaces:**
- Consumes: `broadcast.subscribe/unsubscribe/producer_loop` (Task 5), `health.health_loop` (Task 4), `history.get_full_snapshot` (Task 3)
- Produces: `GET /dashboard/stream` (SSE endpoint), removes `GET /dashboard/telemetry` and `GET /dashboard/network`

- [ ] **Step 1: Write the failing test**

Create `server_admin/tests/test_dashboard_stream.py`:

```python
from fastapi.testclient import TestClient

from server_admin.main import app


def test_dashboard_stream_emits_snapshot_then_tick():
    with TestClient(app) as client:
        with client.stream("GET", "/dashboard/stream") as response:
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-cache"
            assert response.headers["x-accel-buffering"] == "no"

            events = []
            for line in response.iter_lines():
                if line.startswith("event:"):
                    events.append(line)
                if len(events) >= 2:
                    break

    assert events[0] == "event: snapshot"
    assert events[1] == "event: tick"


def test_dashboard_telemetry_and_network_routes_removed():
    with TestClient(app) as client:
        assert client.get("/dashboard/telemetry").status_code == 404
        assert client.get("/dashboard/network").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest server_admin/tests/test_dashboard_stream.py -v`
Expected: FAIL — `/dashboard/stream` returns 404 (route doesn't exist yet), and the "routes removed" test fails since they currently return 200.

- [ ] **Step 3: Modify `main.py`**

Update the import block at the top of `server_admin/main.py`:

```python
import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from . import config, env_vars
from .formatting import format_bytes, format_rate, format_uptime, sparkline_points
from .logs import log_file_path, search_log, tail_log
from .severity import compute_health
from .supervisor import SERVICES, monitor_loop, service_manager
from .telemetry import broadcast, gpu as gpu_telemetry, health as health_telemetry, history
from .telemetry.gpu import get_gpu_telemetry
from .telemetry.system import get_system_telemetry
```

(Note: `get_network_telemetry` is no longer imported — its only caller, `/dashboard/network`, is deleted in this task.)

Replace the `lifespan` function (currently lines 27-42):

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    gpu_telemetry.init_nvml()
    producer_task = asyncio.create_task(broadcast.producer_loop())
    health_task = asyncio.create_task(health_telemetry.health_loop())
    crash_task = asyncio.create_task(monitor_loop())
    try:
        yield
    finally:
        for task in (producer_task, health_task, crash_task):
            task.cancel()
        for task in (producer_task, health_task, crash_task):
            try:
                await task
            except asyncio.CancelledError:
                pass
        gpu_telemetry.shutdown_nvml()
```

Delete the `_percent_severity`/`compute_health` definitions if any remain (Task 2 should have already removed them — verify).

Delete the `/dashboard/telemetry` route (currently `dashboard_telemetry`) and the `/dashboard/network` route (currently `dashboard_network`) entirely.

Add the SSE route (place it near the other `/dashboard/*` routes, after `dashboard()`):

```python
def _sse_event(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.get("/dashboard/stream")
async def dashboard_stream(request: Request):
    async def event_gen():
        q = broadcast.subscribe()
        try:
            yield _sse_event("snapshot", history.get_full_snapshot())
            while True:
                if await request.is_disconnected():
                    break
                payload = await q.get()
                yield _sse_event(payload["type"], payload["data"])
        finally:
            broadcast.unsubscribe(q)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # RunPod's reverse proxy buffers responses by default — without
            # this header the stream appears dead until the buffer flushes.
            # This is the most likely "works locally, silently broken on the
            # pod" failure mode for this feature.
            "X-Accel-Buffering": "no",
        },
    )
```

This route is intentionally **not** added to `LOGIN_EXEMPT_PATHS` — it stays behind `SessionAuthMiddleware` like every other dashboard route. `EventSource` sends cookies automatically, so session auth works without any client-side change.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest server_admin/tests/test_dashboard_stream.py -v`
Expected: `2 passed` (allow a few seconds — the test waits for a real producer tick at the real 2s cadence)

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `.venv/bin/pytest server_admin/tests/ -v`
Expected: all tests pass (diskio, severity, history, health, broadcast, dashboard_stream)

- [ ] **Step 6: Commit**

```bash
git add server_admin/main.py server_admin/tests/test_dashboard_stream.py
git commit -m "feat(server-admin): add SSE dashboard stream, remove polled telemetry/network routes"
```

---

### Task 7: Vendor uPlot and write `charts.js`

**Files:**
- Create: `server_admin/static/uPlot.iife.min.js` (vendored, pinned v1.6.32)
- Create: `server_admin/static/uPlot.min.css` (vendored, pinned v1.6.32)
- Create: `server_admin/static/charts.js`

No automated tests — this is DOM/canvas rendering, verified manually in Task 9. This task's steps are the implementation itself.

- [ ] **Step 1: Vendor uPlot 1.6.32**

Run:
```bash
curl -sfo server_admin/static/uPlot.iife.min.js https://cdn.jsdelivr.net/npm/uplot@1.6.32/dist/uPlot.iife.min.js
curl -sfo server_admin/static/uPlot.min.css https://cdn.jsdelivr.net/npm/uplot@1.6.32/dist/uPlot.min.css
```
Expected: both files created, no error output. Verify: `head -c 200 server_admin/static/uPlot.iife.min.js` shows minified JS starting with a license comment; `wc -c server_admin/static/uPlot.iife.min.js` should be roughly 45-50 KB.

- [ ] **Step 2: Write `charts.js`**

Create `server_admin/static/charts.js`:

```javascript
(function () {
  "use strict";

  // Merges the 3 RRD-style tiers (oldest-to-newest: tier2, tier1, raw) into
  // one continuous series for display. Resolution degrades going further
  // back in time — this is the tradeoff for bounded memory, not a bug.
  function mergeTiers(seriesSnapshot) {
    return seriesSnapshot.tier2.concat(seriesSnapshot.tier1, seriesSnapshot.raw);
  }

  function toUplotData(points) {
    var xs = [];
    var ys = [];
    points.forEach(function (p) {
      xs.push(p[0]);
      ys.push(p[1]);
    });
    return [xs, ys];
  }

  // Drag-to-zoom (x-axis only) + double-click-to-reset, following uPlot's
  // own documented zoom pattern (cursor.drag + a setSelect hook) rather than
  // a separate wheel-zoom plugin file.
  function initChart(containerId, label) {
    var el = document.getElementById(containerId);
    if (!el) return null;

    var chart = new uPlot(
      {
        width: el.clientWidth || 320,
        height: 160,
        cursor: { drag: { x: true, y: false } },
        series: [{}, { label: label, stroke: "#7048e8", width: 2 }],
        scales: { x: { time: true } },
        hooks: {
          setSelect: [
            function (u) {
              if (u.select.width > 5) {
                var min = u.posToVal(u.select.left, "x");
                var max = u.posToVal(u.select.left + u.select.width, "x");
                u.setScale("x", { min: min, max: max });
              }
            },
          ],
        },
      },
      [[], []],
      el
    );

    el.addEventListener("dblclick", function () {
      var xs = chart.data[0];
      if (xs.length > 1) {
        chart.setScale("x", { min: xs[0], max: xs[xs.length - 1] });
      }
    });

    return chart;
  }

  function loadSnapshot(chart, seriesSnapshot) {
    if (!chart || !seriesSnapshot) return;
    chart.setData(toUplotData(mergeTiers(seriesSnapshot)));
  }

  // Client-side point cap mirrors the server's raw-tier size order of
  // magnitude so a long-lived open tab doesn't grow memory unbounded.
  var MAX_CLIENT_POINTS = 2000;

  function appendPoint(chart, ts, value) {
    if (!chart) return;
    var xs = chart.data[0].concat([ts]);
    var ys = chart.data[1].concat([value]);
    if (xs.length > MAX_CLIENT_POINTS) {
      xs = xs.slice(-MAX_CLIENT_POINTS);
      ys = ys.slice(-MAX_CLIENT_POINTS);
    }
    chart.setData([xs, ys]);
  }

  var CHARTS = {
    cpu: null,
    mem: null,
    disk: null,
    netSend: null,
    netRecv: null,
    diskRead: null,
    diskWrite: null,
  };

  function initAllCharts() {
    CHARTS.cpu = initChart("chart-cpu", "CPU %");
    CHARTS.mem = initChart("chart-mem", "Mem %");
    CHARTS.disk = initChart("chart-disk", "Disk %");
    CHARTS.netSend = initChart("chart-net-send", "Send bps");
    CHARTS.netRecv = initChart("chart-net-recv", "Recv bps");
    CHARTS.diskRead = initChart("chart-disk-read", "Read bps");
    CHARTS.diskWrite = initChart("chart-disk-write", "Write bps");
  }

  function loadAll(snapshot) {
    var s = snapshot.series;
    loadSnapshot(CHARTS.cpu, s.cpu_percent);
    loadSnapshot(CHARTS.mem, s.mem_percent);
    loadSnapshot(CHARTS.disk, s.disk_percent);
    loadSnapshot(CHARTS.netSend, s.net_send_bps);
    loadSnapshot(CHARTS.netRecv, s.net_recv_bps);
    loadSnapshot(CHARTS.diskRead, s.disk_read_bps);
    loadSnapshot(CHARTS.diskWrite, s.disk_write_bps);
  }

  function updateAll(tick) {
    var now = Date.now() / 1000;
    appendPoint(CHARTS.cpu, now, tick.system.cpu_percent);
    appendPoint(CHARTS.mem, now, tick.system.mem_percent);
    appendPoint(CHARTS.disk, now, tick.system.disk_percent);
    appendPoint(CHARTS.netSend, now, tick.network.send_rate_bps);
    appendPoint(CHARTS.netRecv, now, tick.network.recv_rate_bps);
    appendPoint(CHARTS.diskRead, now, tick.diskio.read_rate_bps);
    appendPoint(CHARTS.diskWrite, now, tick.diskio.write_rate_bps);
  }

  window.charts = { init: initAllCharts, loadAll: loadAll, updateAll: updateAll };
})();
```

- [ ] **Step 3: Commit**

```bash
git add server_admin/static/uPlot.iife.min.js server_admin/static/uPlot.min.css server_admin/static/charts.js
git commit -m "feat(server-admin): vendor uPlot 1.6.32 and add charts.js"
```

---

### Task 8: Wire templates and `app.js` to the SSE stream

**Files:**
- Modify: `server_admin/templates/base.html`
- Modify: `server_admin/templates/dashboard.html`
- Delete: `server_admin/templates/_dashboard_telemetry.html`
- Delete: `server_admin/templates/_dashboard_network.html`
- Modify: `server_admin/static/app.js`
- Modify: `server_admin/static/style.css`

- [ ] **Step 1: Add uPlot and charts.js to `base.html`**

In `server_admin/templates/base.html`, after line 10 (`<link rel="stylesheet" href="/static/style.css">`) and before line 11 (the htmx script), add:

```html
  <link rel="stylesheet" href="/static/uPlot.min.css">
  <script src="/static/uPlot.iife.min.js"></script>
```

Then change line 12 (`<script src="/static/app.js" defer></script>`) to also load `charts.js` first, so it's defined before `app.js`'s `DOMContentLoaded` handler runs:

```html
  <script src="/static/charts.js" defer></script>
  <script src="/static/app.js" defer></script>
```

- [ ] **Step 2: Rewrite `dashboard.html`**

Replace the full contents of `server_admin/templates/dashboard.html`:

```html
{% extends "base.html" %}
{% block title %}Dashboard — Server Admin{% endblock %}
{% block content %}
<h1>Dashboard</h1>
<div class="metric-grid metric-grid--charts">
  <div class="chart-card"><h3>CPU %</h3><div id="chart-cpu" class="chart"></div></div>
  <div class="chart-card"><h3>Memory %</h3><div id="chart-mem" class="chart"></div></div>
  <div class="chart-card"><h3>Disk %</h3><div id="chart-disk" class="chart"></div></div>
  <div class="chart-card"><h3>Network Sent</h3><div id="chart-net-send" class="chart"></div></div>
  <div class="chart-card"><h3>Network Received</h3><div id="chart-net-recv" class="chart"></div></div>
  <div class="chart-card"><h3>Disk Read</h3><div id="chart-disk-read" class="chart"></div></div>
  <div class="chart-card"><h3>Disk Write</h3><div id="chart-disk-write" class="chart"></div></div>
</div>
<div id="dashboard-gpu" hx-get="/dashboard/gpu" hx-trigger="load, every 3s" hx-swap="innerHTML"></div>
{% endblock %}
```

- [ ] **Step 3: Delete the now-unused partials**

```bash
git rm server_admin/templates/_dashboard_telemetry.html server_admin/templates/_dashboard_network.html
```

- [ ] **Step 4: Add the SSE client to `app.js`**

In `server_admin/static/app.js`, add a new function and wire it into the existing `DOMContentLoaded` listener (currently lines 76-80):

```javascript
  function initTelemetryStream() {
    if (!document.getElementById("chart-cpu")) return; // only on the dashboard page
    window.charts.init();
    var es = new EventSource("/dashboard/stream");
    es.addEventListener("snapshot", function (evt) {
      window.charts.loadAll(JSON.parse(evt.data));
    });
    es.addEventListener("tick", function (evt) {
      window.charts.updateAll(JSON.parse(evt.data));
    });
  }

  document.body.addEventListener("htmx:afterSwap", classifyLogLines);
  document.addEventListener("DOMContentLoaded", function () {
    classifyLogLines();
    initLogFilter();
    initServiceRestartButtons();
    initTelemetryStream();
  });
```

(Replace the existing final `document.addEventListener("DOMContentLoaded", ...)` block — don't add a second one.)

- [ ] **Step 5: Add chart styling to `style.css`**

Append to `server_admin/static/style.css`:

```css
/* Real-time telemetry charts (uPlot) */

.metric-grid--charts {
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
}

.chart-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 1rem 1.25rem;
}

.chart-card h3 {
  font-size: 0.78rem;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin: 0 0 0.5rem;
  font-weight: 500;
}

.chart {
  height: 160px;
}
```

- [ ] **Step 6: Run the full test suite to check for regressions**

Run: `.venv/bin/pytest server_admin/tests/ -v`
Expected: all tests still pass (template/JS changes don't affect Python tests, but confirms nothing else broke)

- [ ] **Step 7: Commit**

```bash
git add server_admin/templates/base.html server_admin/templates/dashboard.html server_admin/static/app.js server_admin/static/style.css
git commit -m "feat(server-admin): wire dashboard page to SSE stream with uPlot charts"
```

---

### Task 9: End-to-end manual verification

No code changes — this is a manual smoke test pass. Not GPU-dependent parts are fully verifiable on a non-GPU dev machine; GPU-dependent parts need a real pod.

**Verifiable locally:**

- [ ] **Step 1: Start the app locally**

Run: `.venv/bin/uvicorn server_admin.main:app --port 8001` (from repo root, with `PYTHONPATH` covering the repo root — since `pytest.ini`'s rootdir insertion doesn't apply outside pytest, run as `.venv/bin/python -m uvicorn server_admin.main:app --port 8001` from the repo root instead so `server_admin` resolves as a package)

Expected: server starts, no traceback on startup (NVML absence degrades to `available: False` per existing `gpu.py` behavior, unchanged by this plan)

- [ ] **Step 2: Confirm the SSE stream emits real data**

Run (in a second terminal, let it run ~5s then Ctrl-C): `curl -N http://127.0.0.1:8001/dashboard/stream`

Expected: an `event: snapshot` block with a JSON body containing `"series"`, `"gpu"`, `"service_vram"` keys, followed by `event: tick` blocks every ~2s containing `"system"`, `"network"`, `"diskio"`, `"gpus": []`, `"services"`, `"health"` keys. `diskio` values should be non-zero after the second tick (real local disk I/O).

- [ ] **Step 3: Confirm the dashboard page loads and charts populate**

Open `http://127.0.0.1:8001/dashboard` in a browser (log in first if `SERVER_ADMIN_USERNAME`/`PASSWORD` happen to be set in your shell env — otherwise auth is disabled and it loads directly). Confirm: 7 chart cards render (CPU/Mem/Disk/Net Sent/Net Recv/Disk Read/Disk Write), each showing a live-updating line within a few seconds, and the GPU section shows "GPU unavailable" (expected without real hardware) rather than crashing the page. Check the browser console for JS errors — there should be none.

- [ ] **Step 4: Confirm multiple tabs/reconnects don't leak subscribers**

Open the dashboard in 2-3 browser tabs simultaneously, then close them one at a time. Between closes, hit `curl -s http://127.0.0.1:8001/health` to confirm the server is still responsive (this doesn't directly show subscriber count, but confirms the server hasn't wedged — if you want to see the count directly, temporarily add `print(len(broadcast._subscribers))` inside `producer_loop`'s loop body, observe it decrement as tabs close, then remove the print before committing anything further).

**Needs a real RTX 5090/4090 pod to verify** (do this after the image is rebuilt and deployed, not as part of this implementation session):

- [ ] **Step 5: Real GPU data**

On a live pod, confirm the GPU section on `/dashboard` shows real utilization/VRAM/temperature once a GPU workload is running in InvokeAI, and that the "Restart to free VRAM" button (unchanged in this pass) still works.

- [ ] **Step 6: RunPod reverse-proxy SSE behavior**

Through the actual RunPod-proxied URL (not a local port-forward), open `/dashboard` and confirm chart ticks arrive roughly every 2s rather than arriving in large delayed batches — this is the scenario the `X-Accel-Buffering: no` header exists to prevent.

- [ ] **Step 7: Health checks against real services**

Confirm `services.invokeai.health`, `services.code-server.health`, and `services["civitai-manager"].health` in the SSE payload (visible via the same `curl -N .../dashboard/stream` from Step 2, run against the pod) report `up: true` with a real `latency_ms` once those services are running.

---

## Self-Review Notes

- **Spec coverage:** real-time charts/UX (Tasks 7-8), historical data & trends with tiered ring buffers (Task 3), additional metrics — disk I/O (Task 1), per-service VRAM trend (Task 3's `_service_vram_series`), per-service uptime (already existed, surfaced via Task 5's `_service_snapshot`), HTTP health checks (Task 4) — all covered. No alerting/SQLite tasks exist, per explicit scope exclusion.
- **Corrected from the initial high-level plan:** per-service uptime was already tracked by `ManagedService._uptime()`/`ServiceStatus.uptime_s` — no new tracking code was needed, only surfacing the existing field in the SSE payload (Task 5). `formatting.py`'s `sparkline_points()` is **not** removed — `_dashboard_gpu.html` still uses it (that route stays on htmx this pass) — the initial plan's file list incorrectly flagged it for removal; this plan leaves it untouched.
- **Type/interface consistency checked:** `history.record()`'s signature (`system, network, diskio, gpu`) matches every call site (`broadcast.build_payload()`); `get_gpu_history(index, tier="raw")`'s default keeps `_dashboard_gpu.html`'s existing unqualified call (`history.get_gpu_history(g.index)`) working unchanged.
