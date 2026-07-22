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
