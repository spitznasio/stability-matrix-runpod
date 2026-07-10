"""Lightweight process supervisor for the three services this image runs.

Generalizes the PID-file pattern from restart_invokeai.sh into pure-stdlib
Python so it can be imported directly by the FastAPI app (in-process status
checks and control) and invoked as a CLI from start.sh (`python3 -m
server_admin.supervisor start invokeai`).
"""

import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

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


SERVICES: dict[str, ServiceSpec] = {
    "invokeai": ServiceSpec(
        key="invokeai",
        display_name="InvokeAI",
        start_cmd=["/usr/local/bin/invokeai-web"],
        match_pattern=r"invokeai-web",
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


class ManagedService:
    def __init__(self, spec: ServiceSpec):
        self.spec = spec

    def status(self) -> ServiceStatus:
        pid = self._read_pid()
        if pid and self._pid_alive(pid):
            return ServiceStatus(running=True, pid=pid, uptime_s=self._uptime(pid))

        discovered = self._discover_pid()
        if discovered:
            self._write_pid(discovered)
            return ServiceStatus(running=True, pid=discovered, uptime_s=self._uptime(discovered))

        self.spec.pid_file.unlink(missing_ok=True)
        return ServiceStatus(running=False, pid=None, uptime_s=None)

    def start(self) -> ServiceStatus:
        current = self.status()
        if current.running:
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
        return ServiceStatus(running=True, pid=proc.pid, uptime_s=0.0)

    def stop(self, timeout: int | None = None) -> ServiceStatus:
        current = self.status()
        if not current.running:
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
        return ServiceStatus(running=False, pid=None, uptime_s=None)

    def restart(self) -> ServiceStatus:
        self.stop()
        return self.start()

    def _read_pid(self) -> int | None:
        try:
            text = self.spec.pid_file.read_text().strip()
        except FileNotFoundError:
            return None
        return int(text) if text.isdigit() else None

    def _write_pid(self, pid: int) -> None:
        self.spec.pid_file.parent.mkdir(parents=True, exist_ok=True)
        self.spec.pid_file.write_text(str(pid))

    @staticmethod
    def _pid_alive(pid: int) -> bool:
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


service_manager = ServiceManager()


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
