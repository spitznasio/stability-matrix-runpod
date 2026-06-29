from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

STATE_DIR = Path("/workspace/onedrive_sync_manager")
JOBS_PATH = STATE_DIR / "jobs.json"
_LOCK = Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_jobs_unlocked() -> list[dict[str, Any]]:
    if not JOBS_PATH.exists():
        return []
    try:
        return json.loads(JOBS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _write_jobs_unlocked(jobs: list[dict[str, Any]]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    JOBS_PATH.write_text(json.dumps(jobs, indent=2), encoding="utf-8")


def create_job(
    local_subpath: str,
    remote_folder: str,
    include_globs: str,
    exclude_globs: str,
    conflict_behavior: str,
    force_rescan: bool,
) -> dict[str, Any]:
    with _LOCK:
        jobs = _read_jobs_unlocked()
        job = {
            "id": str(uuid4()),
            "status": "queued",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "started_at": None,
            "finished_at": None,
            "local_subpath": local_subpath,
            "remote_folder": remote_folder,
            "include_globs": include_globs,
            "exclude_globs": exclude_globs,
            "conflict_behavior": conflict_behavior,
            "force_rescan": force_rescan,
            "progress": {
                "files_total": 0,
                "files_uploaded": 0,
                "files_skipped": 0,
                "files_failed": 0,
                "bytes_total": 0,
                "bytes_uploaded": 0,
            },
            "error": None,
            "events": [],
        }
        jobs.append(job)
        _write_jobs_unlocked(jobs)
        return job


def update_job(job_id: str, **updates: Any) -> dict[str, Any] | None:
    with _LOCK:
        jobs = _read_jobs_unlocked()
        for idx, job in enumerate(jobs):
            if job.get("id") == job_id:
                merged = {**job, **updates, "updated_at": _now_iso()}
                jobs[idx] = merged
                _write_jobs_unlocked(jobs)
                return merged
    return None


def get_job(job_id: str) -> dict[str, Any] | None:
    with _LOCK:
        jobs = _read_jobs_unlocked()
        for job in jobs:
            if job.get("id") == job_id:
                return job
    return None


def get_latest_job() -> dict[str, Any] | None:
    with _LOCK:
        jobs = _read_jobs_unlocked()
        if not jobs:
            return None
        return sorted(jobs, key=lambda j: j.get("created_at", ""))[-1]


def get_recent_jobs(limit: int = 10) -> list[dict[str, Any]]:
    with _LOCK:
        jobs = _read_jobs_unlocked()
        if not jobs:
            return []
        return sorted(jobs, key=lambda j: j.get("created_at", ""), reverse=True)[:limit]


def get_jobs_page(page: int = 1, page_size: int = 20) -> dict[str, Any]:
    with _LOCK:
        jobs = sorted(_read_jobs_unlocked(), key=lambda j: j.get("created_at", ""), reverse=True)

    total = len(jobs)
    page_size = max(1, page_size)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    end = start + page_size

    return {
        "items": jobs[start:end],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_page": page - 1,
        "next_page": page + 1,
    }


def append_job_event(job_id: str, message: str) -> dict[str, Any] | None:
    with _LOCK:
        jobs = _read_jobs_unlocked()
        for idx, job in enumerate(jobs):
            if job.get("id") != job_id:
                continue
            events = list(job.get("events", []))
            events.append({"at": _now_iso(), "message": message})
            events = events[-200:]
            merged = {
                **job,
                "events": events,
                "updated_at": _now_iso(),
            }
            jobs[idx] = merged
            _write_jobs_unlocked(jobs)
            return merged
    return None
