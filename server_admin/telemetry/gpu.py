import shutil
import subprocess

_QUERY_FIELDS = (
    "index,name,utilization.gpu,utilization.memory,"
    "memory.used,memory.total,temperature.gpu,power.draw"
)


def get_gpu_telemetry() -> dict:
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return {"available": False, "reason": "nvidia-smi not found on PATH", "gpus": []}

    try:
        result = subprocess.run(
            [nvidia_smi, f"--query-gpu={_QUERY_FIELDS}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return {"available": False, "reason": str(exc), "gpus": []}

    gpus = []
    for line in result.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 8:
            continue
        index, name, util_gpu, util_mem, mem_used, mem_total, temp, power = parts
        gpus.append(
            {
                "index": int(index),
                "name": name,
                "utilization_gpu": float(util_gpu),
                "utilization_mem": float(util_mem),
                "memory_used_mb": float(mem_used),
                "memory_total_mb": float(mem_total),
                "temperature_c": float(temp),
                "power_draw_w": None if power in ("[N/A]", "N/A") else float(power),
            }
        )

    if not gpus:
        return {"available": False, "reason": "nvidia-smi returned no GPUs", "gpus": []}

    return {"available": True, "reason": None, "gpus": gpus}
