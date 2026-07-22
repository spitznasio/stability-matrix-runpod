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
