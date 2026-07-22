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
