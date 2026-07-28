"""Lightweight process supervisor for the three services this image runs.

Generalizes the PID-file pattern from restart_invokeai.sh into pure-stdlib
Python so it can be imported directly by the FastAPI app (in-process status
checks and control) and invoked as a CLI from start.sh (`python3 -m
server_admin.supervisor start invokeai`).
"""

import asyncio
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import psutil
from starlette.concurrency import run_in_threadpool

from . import config

STATE_DIR = Path(os.environ.get("SERVER_ADMIN_STATE_DIR", "/tmp/server-admin"))
LOG_DIR = STATE_DIR / "logs"


@dataclass(frozen=True)
class ServiceSpec:
    key: str
    display_name: str
    start_cmd: list[str]
    # Regex matched against each process's /proc/<pid>/cmdline, used to adopt
    # a process that's running but whose PID file is missing or stale.
    match_pattern: str
    stop_signal: int = signal.SIGINT
    stop_timeout: int = 60
    cwd: str | None = None
    env_overrides: dict[str, str] = field(default_factory=dict)

    @property
    def pid_file(self) -> Path:
        return STATE_DIR / f"{self.key}.pid"

    @property
    def log_file(self) -> Path:
        return LOG_DIR / f"{self.key}.log"

    @property
    def desired_state_file(self) -> Path:
        return STATE_DIR / f"{self.key}.desired"


SERVICES: dict[str, ServiceSpec] = {
    "invokeai": ServiceSpec(
        key="invokeai",
        display_name="InvokeAI",
        start_cmd=["/usr/local/bin/invokeai-web"],
        match_pattern=r"invokeai-web",
        # Ensure SSH/manual supervisor restarts keep the same externally
        # reachable/networked behavior as container boot defaults.
        env_overrides={
            "INVOKEAI_ROOT": os.environ.get("INVOKEAI_ROOT", "/workspace/invokeai"),
            "INVOKEAI_HOST": os.environ.get("INVOKEAI_HOST", "0.0.0.0"),
            "INVOKEAI_PORT": os.environ.get("INVOKEAI_PORT", "9090"),
        },
    ),
    "code-server": ServiceSpec(
        key="code-server",
        display_name="code-server",
        start_cmd=[
            "code-server",
            "--bind-addr",
            "0.0.0.0:8080",
            "--auth",
            "none",
            "--disable-telemetry",
            "/workspace",
        ],
        match_pattern=r"code-server",
    ),
    "civitai-manager": ServiceSpec(
        key="civitai-manager",
        display_name="CivitAI Manager",
        start_cmd=[
            "uvicorn",
            "civitai_manager.main:app",
            "--app-dir",
            "/opt",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
        ],
        # Deliberately specific (not a bare "uvicorn" match) so this never
        # adopts/kills a different uvicorn process in the same container.
        match_pattern=r"civitai_manager\.main:app",
    ),
    "onedrive-sync": ServiceSpec(
        key="onedrive-sync",
        display_name="OneDrive Sync Manager",
        start_cmd=[
            "uvicorn",
            "onedrive_sync_manager.main:app",
            "--app-dir",
            "/opt",
            "--host",
            "0.0.0.0",
            "--port",
            "8002",
        ],
        # Deliberately specific (not a bare "uvicorn" match) so this never
        # adopts/kills a different uvicorn process in the same container.
        match_pattern=r"onedrive_sync_manager\.main:app",
    ),
    "aria2-rpc": ServiceSpec(
        key="aria2-rpc",
        display_name="aria2 (download daemon)",
        start_cmd=[
            "aria2c",
            "--enable-rpc",
            "--rpc-listen-all=false",
            "--rpc-listen-port=6800",
            f"--rpc-secret={os.environ.get('ARIA2_RPC_SECRET', '')}",
            "--dir=/workspace/civitai-downloads",
            "--continue=true",
            "--max-connection-per-server=16",
            "--split=16",
            "--min-split-size=1M",
            "--max-concurrent-downloads=3",
            "--max-tries=5",
            "--retry-wait=5",
            "--save-session=/tmp/server-admin/aria2.session",
            "--save-session-interval=30",
            "--allow-overwrite=true",
        ],
        match_pattern=r"aria2c.*--enable-rpc",
    ),
}


@dataclass
class ServiceStatus:
    running: bool
    pid: int | None
    uptime_s: float | None
    desired: str = "stopped"
    crashed: bool = False


class ManagedService:
    # Cache of psutil.Process instances keyed by pid, shared across all
    # ManagedService instances so resource_usage() polls get a real
    # cpu_percent() delta instead of the always-0.0 first read. create_time()
    # is checked to detect pid reuse (a new, unrelated process landing on a
    # recycled pid).
    _psutil_cache: dict[int, psutil.Process] = {}

    def __init__(self, spec: ServiceSpec):
        self.spec = spec

    def status(self) -> ServiceStatus:
        pid = self._read_pid()
        if pid and self._pid_alive(pid):
            return self._running_status(pid)

        discovered = self._discover_pid()
        if discovered:
            self._write_pid(discovered)
            return self._running_status(discovered)

        self.spec.pid_file.unlink(missing_ok=True)
        desired = self._read_desired(default_if_missing="stopped")
        return ServiceStatus(running=False, pid=None, uptime_s=None, desired=desired, crashed=desired == "running")

    def _running_status(self, pid: int) -> ServiceStatus:
        desired = self._read_desired(default_if_missing="running")
        return ServiceStatus(running=True, pid=pid, uptime_s=self._uptime(pid), desired=desired, crashed=False)

    def start(self) -> ServiceStatus:
        current = self.status()
        if current.running:
            self._write_desired("running")
            return current

        self.spec.log_file.parent.mkdir(parents=True, exist_ok=True)
        log_fh = open(self.spec.log_file, "ab")
        proc = subprocess.Popen(
            self.spec.start_cmd,
            stdout=log_fh,
            stderr=log_fh,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=self.spec.cwd,
            env={**os.environ, **self.spec.env_overrides},
        )
        self._write_pid(proc.pid)
        self._write_desired("running")
        return ServiceStatus(running=True, pid=proc.pid, uptime_s=0.0, desired="running", crashed=False)

    def stop(self, timeout: int | None = None) -> ServiceStatus:
        current = self.status()
        if not current.running:
            self._write_desired("stopped")
            return current

        timeout = timeout if timeout is not None else self.spec.stop_timeout
        pid = current.pid
        assert pid is not None

        os.kill(pid, self.spec.stop_signal)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self._pid_alive(pid):
                break
            time.sleep(1)
        else:
            os.kill(pid, signal.SIGTERM)
            time.sleep(2)
            if self._pid_alive(pid):
                os.kill(pid, signal.SIGKILL)

        self.spec.pid_file.unlink(missing_ok=True)
        self._write_desired("stopped")
        return ServiceStatus(running=False, pid=None, uptime_s=None, desired="stopped", crashed=False)

    def restart(self) -> ServiceStatus:
        self.stop()
        return self.start()

    def resource_usage(self, pid: int) -> dict | None:
        """Returns {"cpu_percent": float, "rss_mb": float} for a running pid,
        or None if the process is gone. The first call after a pid first
        appears in the cache seeds cpu_percent() and reports 0.0 outright —
        real deltas show up starting the following poll. (Calling
        cpu_percent() a second time immediately after the seed, rather than
        deferring to the next poll, would measure a near-zero wall-clock
        interval and produce a noisy/inflated reading instead of a clean
        0.0, so the seed call's result is used directly rather than
        discarded.) psutil.Process pins the pid's create_time() at
        construction and raises NoSuchProcess on later calls if that pid was
        reused by an unrelated process, so no separate reuse-detection is
        needed here."""
        proc = self._psutil_cache.get(pid)
        if proc is None:
            try:
                proc = psutil.Process(pid)
                seed_cpu_percent = proc.cpu_percent(interval=None)
                rss_mb = proc.memory_info().rss / (1024 * 1024)
            except psutil.NoSuchProcess:
                return None
            self._psutil_cache[pid] = proc
            return {"cpu_percent": seed_cpu_percent, "rss_mb": rss_mb}

        try:
            cpu_percent = proc.cpu_percent(interval=None)
            rss_mb = proc.memory_info().rss / (1024 * 1024)
        except psutil.NoSuchProcess:
            self._psutil_cache.pop(pid, None)
            return None
        return {"cpu_percent": cpu_percent, "rss_mb": rss_mb}

    def _read_pid(self) -> int | None:
        try:
            text = self.spec.pid_file.read_text().strip()
        except FileNotFoundError:
            return None
        return int(text) if text.isdigit() else None

    def _write_pid(self, pid: int) -> None:
        self.spec.pid_file.parent.mkdir(parents=True, exist_ok=True)
        self.spec.pid_file.write_text(str(pid))

    def _read_desired(self, *, default_if_missing: str) -> str:
        try:
            value = self.spec.desired_state_file.read_text().strip()
        except FileNotFoundError:
            return default_if_missing
        return value if value in ("running", "stopped") else default_if_missing

    def _write_desired(self, value: str) -> None:
        self.spec.desired_state_file.parent.mkdir(parents=True, exist_ok=True)
        self.spec.desired_state_file.write_text(value)

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        # A killed child we spawned (via start()) becomes a zombie until
        # reaped — kill(pid, 0) alone reports zombies as alive indefinitely,
        # since the PID stays allocated until wait() collects it. Reap it
        # here via WNOHANG so a crashed service is detected promptly rather
        # than only after some unrelated Popen call elsewhere happens to
        # reap it. ECHILD means pid isn't our direct child (e.g. adopted via
        # /proc scanning), in which case fall back to the kill(pid, 0) check.
        try:
            reaped_pid, _status = os.waitpid(pid, os.WNOHANG)
            if reaped_pid == pid:
                return False
        except ChildProcessError:
            pass

        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return False
        return True

    def _discover_pid(self) -> int | None:
        pattern = re.compile(self.spec.match_pattern)
        proc_dir = Path("/proc")
        if not proc_dir.is_dir():
            return None
        for entry in proc_dir.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                cmdline = entry.joinpath("cmdline").read_bytes().replace(b"\x00", b" ").decode(
                    "utf-8", errors="replace"
                )
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
            if pattern.search(cmdline):
                return int(entry.name)
        return None

    @staticmethod
    def _uptime(pid: int) -> float | None:
        try:
            mtime = Path(f"/proc/{pid}").stat().st_mtime
        except (FileNotFoundError, PermissionError):
            return None
        return max(0.0, time.time() - mtime)


class ServiceManager:
    def __init__(self):
        self._services = {key: ManagedService(spec) for key, spec in SERVICES.items()}

    def all_statuses(self) -> dict[str, ServiceStatus]:
        return {key: svc.status() for key, svc in self._services.items()}

    def get(self, key: str) -> ManagedService:
        return self._services[key]

    def lookup_service_by_pid(self, pid: int, statuses: dict[str, ServiceStatus] | None = None) -> str | None:
        statuses = statuses if statuses is not None else self.all_statuses()
        for key, status in statuses.items():
            if status.pid == pid:
                return key
        return None

    def check_and_recover(self) -> list[str]:
        """Restarts any crashed service whose key is in the auto-restart
        allowlist. Kept separate from status()/all_statuses() so those stay
        side-effect-free reads; call this only from the periodic monitor."""
        recovered = []
        for key, status in self.all_statuses().items():
            if status.crashed and key in config.AUTO_RESTART_SERVICES:
                self._services[key].start()
                recovered.append(key)
        return recovered


service_manager = ServiceManager()


async def monitor_loop(interval_s: int | None = None) -> None:
    """Background task: periodically checks for crashed services and
    auto-restarts the ones on the allowlist. Runs as its own asyncio task
    (started from main.py's lifespan) rather than piggybacking on page-poll
    requests, since the latter would silently stop recovering services
    whenever no browser tab is open."""
    interval_s = interval_s if interval_s is not None else config.CRASH_MONITOR_INTERVAL_S
    while True:
        try:
            await run_in_threadpool(service_manager.check_and_recover)
        except Exception as exc:
            print(f"[server-admin] crash monitor loop error: {exc}", file=sys.stderr)
        await asyncio.sleep(interval_s)


def _cli() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in {"start", "stop", "restart", "status"}:
        print("usage: python3 -m server_admin.supervisor <start|stop|restart|status> <service-key>", file=sys.stderr)
        raise SystemExit(2)

    action, key = sys.argv[1], sys.argv[2]
    try:
        svc = service_manager.get(key)
    except KeyError:
        print(f"unknown service key: {key!r} (known: {', '.join(SERVICES)})", file=sys.stderr)
        raise SystemExit(2)

    result = getattr(svc, action)()
    print(result)


if __name__ == "__main__":
    _cli()
