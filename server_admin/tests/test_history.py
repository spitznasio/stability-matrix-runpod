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
