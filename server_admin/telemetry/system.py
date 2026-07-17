import os
import time
from pathlib import Path

import psutil

# Preferred over "/" so disk usage reflects the pod's volume-disk mount rather than
# the container's root layer. Note this still reports the underlying block device's
# total capacity, not a true per-pod storage quota -- there's no cgroup or RunPod API
# for that, so this is a known limitation rather than something fixable here.
DISK_PATH = "/workspace" if Path("/workspace").exists() else "/"

CGROUP_V2_MEM_MAX = Path("/sys/fs/cgroup/memory.max")
CGROUP_V2_MEM_CURRENT = Path("/sys/fs/cgroup/memory.current")
CGROUP_V1_MEM_LIMIT = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")
CGROUP_V1_MEM_USAGE = Path("/sys/fs/cgroup/memory/memory.usage_in_bytes")

CGROUP_V2_CPU_MAX = Path("/sys/fs/cgroup/cpu.max")
CGROUP_V2_CPU_STAT = Path("/sys/fs/cgroup/cpu.stat")
CGROUP_V1_CPU_QUOTA = Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
CGROUP_V1_CPU_PERIOD = Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
CGROUP_V1_CPUACCT_USAGE = Path("/sys/fs/cgroup/cpuacct/cpuacct.usage")

CPU_SAMPLE_INTERVAL_S = 0.2


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _read_cgroup_v2_usage_usec() -> int | None:
    try:
        for line in CGROUP_V2_CPU_STAT.read_text().splitlines():
            key, _, value = line.partition(" ")
            if key == "usage_usec":
                return int(value)
    except (OSError, ValueError):
        return None
    return None


def _read_cgroup_memory() -> dict | None:
    if CGROUP_V2_MEM_MAX.exists():
        limit_raw = None
        try:
            limit_raw = CGROUP_V2_MEM_MAX.read_text().strip()
        except OSError:
            return None
        used = _read_int(CGROUP_V2_MEM_CURRENT)
        if used is None:
            return None
        if limit_raw == "max":
            total = psutil.virtual_memory().total
        else:
            try:
                total = int(limit_raw)
            except ValueError:
                return None
        percent = (used / total * 100) if total else 0.0
        return {
            "mem_total": total,
            "mem_used": used,
            "mem_percent": percent,
            "source": "cgroup_v2",
        }

    if CGROUP_V1_MEM_LIMIT.exists():
        limit = _read_int(CGROUP_V1_MEM_LIMIT)
        used = _read_int(CGROUP_V1_MEM_USAGE)
        if limit is None or used is None:
            return None
        host_total = psutil.virtual_memory().total
        # v1 reports a huge sentinel (e.g. 9223372036854771712) when unlimited.
        total = host_total if limit > host_total else limit
        percent = (used / total * 100) if total else 0.0
        return {
            "mem_total": total,
            "mem_used": used,
            "mem_percent": percent,
            "source": "cgroup_v1",
        }

    return None


def _read_cgroup_cpu() -> dict | None:
    if CGROUP_V2_CPU_MAX.exists():
        try:
            quota_raw, period_raw = CGROUP_V2_CPU_MAX.read_text().split()
            period = int(period_raw)
        except (OSError, ValueError):
            return None
        if quota_raw == "max":
            cpu_count = psutil.cpu_count() or 1
        else:
            try:
                cpu_count = max(int(quota_raw) / period, 1e-9)
            except ValueError:
                return None

        usage_before = _read_cgroup_v2_usage_usec()
        if usage_before is None:
            return None
        time.sleep(CPU_SAMPLE_INTERVAL_S)
        usage_after = _read_cgroup_v2_usage_usec()
        if usage_after is None:
            return None

        delta_usec = usage_after - usage_before
        interval_usec = CPU_SAMPLE_INTERVAL_S * 1_000_000
        percent = (delta_usec / interval_usec) / cpu_count * 100
        return {
            "cpu_percent": max(0.0, min(percent, 100.0)),
            "cpu_count": cpu_count,
            "source": "cgroup_v2",
        }

    if CGROUP_V1_CPU_QUOTA.exists() and CGROUP_V1_CPU_PERIOD.exists():
        quota = _read_int(CGROUP_V1_CPU_QUOTA)
        period = _read_int(CGROUP_V1_CPU_PERIOD)
        if quota is None or period is None:
            return None
        if quota <= 0:
            cpu_count = psutil.cpu_count() or 1
        else:
            cpu_count = max(quota / period, 1e-9)

        usage_before = _read_int(CGROUP_V1_CPUACCT_USAGE)
        if usage_before is None:
            return None
        time.sleep(CPU_SAMPLE_INTERVAL_S)
        usage_after = _read_int(CGROUP_V1_CPUACCT_USAGE)
        if usage_after is None:
            return None

        delta_ns = usage_after - usage_before
        interval_ns = CPU_SAMPLE_INTERVAL_S * 1_000_000_000
        percent = (delta_ns / interval_ns) / cpu_count * 100
        return {
            "cpu_percent": max(0.0, min(percent, 100.0)),
            "cpu_count": cpu_count,
            "source": "cgroup_v1",
        }

    return None


def get_system_telemetry() -> dict:
    disk = psutil.disk_usage(DISK_PATH)
    try:
        # Load average has no meaningful cgroup-scoped equivalent (it's a
        # kernel-wide scheduler metric), so this intentionally stays host-wide.
        load_avg = os.getloadavg()
    except (OSError, AttributeError):
        load_avg = (0.0, 0.0, 0.0)

    mem = _read_cgroup_memory()
    if mem is None:
        vm = psutil.virtual_memory()
        mem = {
            "mem_total": vm.total,
            "mem_used": vm.used,
            "mem_percent": vm.percent,
            "source": "host",
        }

    cpu = _read_cgroup_cpu()
    if cpu is None:
        cpu = {
            "cpu_percent": psutil.cpu_percent(interval=CPU_SAMPLE_INTERVAL_S),
            "cpu_count": psutil.cpu_count() or 1,
            "source": "host",
        }

    return {
        "cpu_percent": cpu["cpu_percent"],
        "cpu_count": round(cpu["cpu_count"], 2),
        "cpu_source": cpu["source"],
        "load_avg": load_avg,
        "mem_total": mem["mem_total"],
        "mem_used": mem["mem_used"],
        "mem_percent": mem["mem_percent"],
        "mem_source": mem["source"],
        "disk_path": DISK_PATH,
        "disk_total": disk.total,
        "disk_used": disk.used,
        "disk_percent": disk.percent,
    }
