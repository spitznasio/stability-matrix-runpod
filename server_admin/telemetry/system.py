import os
from pathlib import Path

import psutil

DISK_PATH = "/workspace" if Path("/workspace").exists() else "/"


def get_system_telemetry() -> dict:
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage(DISK_PATH)
    try:
        load_avg = os.getloadavg()
    except (OSError, AttributeError):
        load_avg = (0.0, 0.0, 0.0)

    return {
        "cpu_percent": psutil.cpu_percent(interval=0.2),
        "cpu_count": psutil.cpu_count() or 1,
        "load_avg": load_avg,
        "mem_total": vm.total,
        "mem_used": vm.used,
        "mem_percent": vm.percent,
        "disk_path": DISK_PATH,
        "disk_total": disk.total,
        "disk_used": disk.used,
        "disk_percent": disk.percent,
    }
