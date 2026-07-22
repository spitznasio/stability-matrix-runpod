import psutil
import pynvml

# NVML represents "value not available" for some per-process fields (notably
# usedGpuMemory on older drivers) as this sentinel rather than raising.
_NVML_VALUE_NOT_AVAILABLE = 2**64 - 1

_available: bool | None = None  # tri-state: None = init_nvml() not yet called
_unavailable_reason: str | None = None


def init_nvml() -> None:
    """Called once from main.py's lifespan startup. Safe to call again after
    shutdown_nvml() (e.g. lazy re-init on a driver hiccup)."""
    global _available, _unavailable_reason
    try:
        pynvml.nvmlInit()
        _available = True
        _unavailable_reason = None
    except pynvml.NVMLError as exc:
        _available = False
        _unavailable_reason = str(exc)


def shutdown_nvml() -> None:
    global _available
    if _available:
        try:
            pynvml.nvmlShutdown()
        except pynvml.NVMLError:
            pass
    _available = False


def _unavailable(reason: str) -> dict:
    return {"available": False, "reason": reason, "gpus": []}


def get_gpu_telemetry(pid_to_service: dict[int, str] | None = None) -> dict:
    if _available is None:
        init_nvml()

    if not _available:
        # One lazy re-init attempt, in case the driver came up after our
        # first failed init (or a prior transient NVMLError shut us down).
        init_nvml()
        if not _available:
            return _unavailable(_unavailable_reason or "NVML unavailable")

    pid_to_service = pid_to_service or {}
    try:
        count = pynvml.nvmlDeviceGetCount()
        gpus = [_read_gpu(i, pid_to_service) for i in range(count)]
    except pynvml.NVMLError as exc:
        # Driver may have restarted under us — drop the stale handle set and
        # degrade for this poll cycle rather than crashing the caller.
        shutdown_nvml()
        return _unavailable(str(exc))

    if not gpus:
        return _unavailable("No GPUs reported by NVML")

    return {"available": True, "reason": None, "gpus": gpus}


def _read_gpu(index: int, pid_to_service: dict[int, str]) -> dict:
    handle = pynvml.nvmlDeviceGetHandleByIndex(index)
    name = pynvml.nvmlDeviceGetName(handle)
    util = pynvml.nvmlDeviceGetUtilizationRates(handle)
    mem = pynvml.nvmlDeviceGetMemoryInfo(handle)

    try:
        temperature_c = float(pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU))
    except pynvml.NVMLError:
        temperature_c = None

    try:
        power_draw_w = pynvml.nvmlDeviceGetPowerUsage(handle) / 1000.0
    except pynvml.NVMLError:
        power_draw_w = None

    return {
        "index": index,
        "name": name,
        "utilization_gpu": float(util.gpu),
        "utilization_mem": float(util.memory),
        "memory_used_mb": mem.used / (1024 * 1024),
        "memory_total_mb": mem.total / (1024 * 1024),
        "temperature_c": temperature_c,
        "power_draw_w": power_draw_w,
        "clocks": _read_clocks(handle),
        "ecc_errors": _read_ecc_errors(handle),
        "processes": _get_processes(handle, pid_to_service),
    }


def _read_clocks(handle) -> dict | None:
    try:
        return {
            "graphics_mhz": pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_GRAPHICS),
            "sm_mhz": pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_SM),
            "mem_mhz": pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_MEM),
        }
    except pynvml.NVMLError:
        return None


def _read_ecc_errors(handle) -> int | None:
    # Genuinely populated on cards that support ECC (H100, H200, RTX PRO 6000
    # series, B200, etc.) — only omitted (via NVMLError, typically
    # NVML_ERROR_NOT_SUPPORTED) on consumer cards like the 4090/5090 that
    # lack ECC-capable VRAM. Not hardcoded off based on the two card models
    # this repo currently ships images for.
    try:
        return pynvml.nvmlDeviceGetTotalEccErrors(
            handle, pynvml.NVML_MEMORY_ERROR_TYPE_UNCORRECTED, pynvml.NVML_AGGREGATE_ECC
        )
    except pynvml.NVMLError:
        return None


def _get_processes(handle, pid_to_service: dict[int, str]) -> list[dict]:
    by_pid: dict[int, int | None] = {}
    for getter in (pynvml.nvmlDeviceGetComputeRunningProcesses, pynvml.nvmlDeviceGetGraphicsRunningProcesses):
        try:
            for proc in getter(handle):
                used = proc.usedGpuMemory
                if used is None or used == _NVML_VALUE_NOT_AVAILABLE:
                    used = None
                # A process can appear in both compute and graphics lists;
                # keep whichever reading actually has a memory figure.
                if proc.pid not in by_pid or by_pid[proc.pid] is None:
                    by_pid[proc.pid] = used
        except pynvml.NVMLError:
            continue

    processes = []
    for pid, used_memory_bytes in by_pid.items():
        try:
            name = psutil.Process(pid).name()
        except psutil.NoSuchProcess:
            name = None
        processes.append(
            {
                "pid": pid,
                "name": name,
                "used_memory_mb": None if used_memory_bytes is None else used_memory_bytes / (1024 * 1024),
                "service_key": pid_to_service.get(pid),
            }
        )
    return processes
