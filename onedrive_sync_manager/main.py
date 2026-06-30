import secrets
from datetime import datetime, timezone
from pathlib import Path
import asyncio

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from . import config
from .onedrive_oauth import (
    acquire_access_token_silent,
    complete_device_flow,
    disconnect,
    get_device_flow,
)
from .job_store import (
    append_job_event,
    create_job,
    get_job,
    get_jobs_page,
    get_latest_job,
    get_recent_jobs,
    update_job,
)
from .sync_engine import build_sync_plan, execute_sync_plan

LOGIN_EXEMPT_PATHS = {"/login", "/health", "/static"}
SYNC_TASKS: dict[str, asyncio.Task] = {}


class SessionAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in LOGIN_EXEMPT_PATHS or path.startswith("/static/"):
            return await call_next(request)
        if request.session.get("authenticated"):
            return await call_next(request)
        return RedirectResponse(url=f"/login?next={path}", status_code=303)


def _ensure_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if token:
        return token
    token = secrets.token_urlsafe(32)
    request.session["csrf_token"] = token
    return token


def _validate_csrf_token(request: Request, token: str) -> bool:
    expected = request.session.get("csrf_token")
    return bool(expected) and secrets.compare_digest(expected, token)


BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="OneDrive Sync Manager")
app.add_middleware(SessionAuthMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SESSION_SECRET,
    session_cookie=config.SESSION_COOKIE,
    https_only=True,
    same_site="lax",
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.on_event("startup")
def startup_validate_config() -> None:
    config.validate_required_auth_config()
    config.validate_required_oauth_config()


async def _get_onedrive_status() -> dict:
    token_result = acquire_access_token_silent()
    if not token_result or "access_token" not in token_result:
        return {"connected": False, "error": None, "account": None}

    headers = {"Authorization": f"Bearer {token_result['access_token']}"}
    async with httpx.AsyncClient(timeout=15) as client:
        me_resp = await client.get("https://graph.microsoft.com/v1.0/me", headers=headers)
    if me_resp.status_code >= 400:
        return {
            "connected": False,
            "error": f"Graph API error ({me_resp.status_code}) while loading profile.",
            "account": None,
        }
    me_data = me_resp.json()
    return {
        "connected": True,
        "error": None,
        "account": me_data.get("userPrincipalName") or me_data.get("mail") or me_data.get("displayName"),
    }


@app.get("/", response_class=HTMLResponse)
async def home() -> RedirectResponse:
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, next: str = "/dashboard"):
    token = _ensure_csrf_token(request)
    if request.session.get("authenticated"):
        return RedirectResponse(url="/dashboard", status_code=303)
    next_url = next if next.startswith("/") else "/dashboard"
    return templates.TemplateResponse(
        request,
        "login.html",
        {"next": next_url, "error": None, "csrf_token": token},
    )


@app.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    next: str = Form("/dashboard"),
):
    if not _validate_csrf_token(request, csrf_token):
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "next": "/dashboard",
                "error": "Invalid form token. Refresh and try again.",
                "csrf_token": _ensure_csrf_token(request),
            },
            status_code=400,
        )

    next_url = next if next.startswith("/") else "/dashboard"
    valid_user = config.AUTH_USERNAME and secrets.compare_digest(username, config.AUTH_USERNAME)
    valid_password = config.verify_password(password)

    if valid_user and valid_password:
        request.session["authenticated"] = True
        request.session["username"] = config.AUTH_USERNAME
        return RedirectResponse(url=next_url, status_code=303)

    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "next": next_url,
            "error": "Invalid username or password.",
            "csrf_token": _ensure_csrf_token(request),
        },
        status_code=401,
    )


@app.post("/logout")
async def logout(request: Request, csrf_token: str = Form(...)) -> RedirectResponse:
    if not _validate_csrf_token(request, csrf_token):
        return RedirectResponse(url="/dashboard", status_code=303)
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    token = _ensure_csrf_token(request)
    onedrive = await _get_onedrive_status()
    dry_run = request.session.get("last_dry_run")
    latest_job = get_latest_job()
    oauth_error = request.session.get("oauth_error")

    defaults = {
        "local_subpath": request.session.get("sync_local_subpath", ""),
        "remote_folder": request.session.get("sync_remote_folder", ""),
        "include_globs": request.session.get("sync_include_globs", "*"),
        "exclude_globs": request.session.get("sync_exclude_globs", "*.tmp,*.part,__pycache__/*,.git/*"),
        "conflict_behavior": request.session.get("sync_conflict_behavior", "replace"),
        "force_rescan": request.session.get("sync_force_rescan", False),
    }

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "username": request.session.get("username", "admin"),
            "csrf_token": token,
            "onedrive": onedrive,
            "oauth_error": oauth_error,
            "dry_run": dry_run,
            "latest_job": latest_job,
            "recent_jobs": get_recent_jobs(limit=12),
            "sync_defaults": defaults,
        },
    )


@app.get("/sync/jobs", response_class=HTMLResponse)
async def sync_jobs_page(request: Request, page: int = 1, page_size: int = 20):
    token = _ensure_csrf_token(request)
    pager = get_jobs_page(page=page, page_size=min(max(page_size, 5), 100))
    active_statuses = {"queued", "running", "cancelling"}
    has_active_jobs = any((job.get("status") in active_statuses) for job in pager.get("items", []))
    return templates.TemplateResponse(
        request,
        "jobs.html",
        {
            "csrf_token": token,
            "pager": pager,
            "has_active_jobs": has_active_jobs,
        },
    )


@app.get("/sync/jobs/{job_id}/view", response_class=HTMLResponse)
async def sync_job_view(request: Request, job_id: str):
    token = _ensure_csrf_token(request)
    job = get_job(job_id)
    if not job:
        return RedirectResponse(url="/sync/jobs", status_code=303)
    job_is_active = job.get("status") in {"queued", "running", "cancelling"}
    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {
            "csrf_token": token,
            "job": job,
            "job_is_active": job_is_active,
        },
    )


@app.get("/auth/connect")
async def auth_connect(request: Request) -> RedirectResponse:
    flow = get_device_flow()
    if "user_code" not in flow:
        request.session["oauth_error"] = flow.get("error_description", "Unable to start device sign-in.")
        return RedirectResponse(url="/dashboard", status_code=303)

    request.session["oauth_device_flow"] = flow
    return templates.TemplateResponse(
        request,
        "connect_device_code.html",
        {
            "csrf_token": _ensure_csrf_token(request),
            "verification_uri": flow.get("verification_uri") or flow.get("verification_uri_complete"),
            "verification_uri_complete": flow.get("verification_uri_complete"),
            "user_code": flow.get("user_code"),
            "expires_in": flow.get("expires_in"),
            "interval": flow.get("interval"),
        },
    )


@app.post("/auth/connect/complete")
async def auth_connect_complete(request: Request, csrf_token: str = Form(...)) -> RedirectResponse:
    if not _validate_csrf_token(request, csrf_token):
        return RedirectResponse(url="/dashboard", status_code=303)

    flow = request.session.get("oauth_device_flow")
    if not flow:
        request.session["oauth_error"] = "Missing device sign-in state. Start the connection again."
        return RedirectResponse(url="/dashboard", status_code=303)

    result = complete_device_flow(flow)
    request.session.pop("oauth_device_flow", None)

    if "access_token" not in result:
        request.session["oauth_error"] = result.get("error_description", "OAuth sign-in failed.")
    else:
        request.session.pop("oauth_error", None)
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/auth/disconnect")
async def auth_disconnect(request: Request, csrf_token: str = Form(...)) -> RedirectResponse:
    if not _validate_csrf_token(request, csrf_token):
        return RedirectResponse(url="/dashboard", status_code=303)
    disconnect()
    request.session.pop("oauth_error", None)
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/auth/status")
async def auth_status(request: Request):
    onedrive = await _get_onedrive_status()
    if request.session.get("oauth_error"):
        onedrive["error"] = request.session["oauth_error"]
    return onedrive


@app.post("/sync/dry-run")
async def sync_dry_run(
    request: Request,
    csrf_token: str = Form(...),
    local_subpath: str = Form(""),
    remote_folder: str = Form(""),
    include_globs: str = Form("*"),
    exclude_globs: str = Form("*.tmp,*.part,__pycache__/*,.git/*"),
    conflict_behavior: str = Form("replace"),
    force_rescan: str | None = Form(None),
):
    if not _validate_csrf_token(request, csrf_token):
        return RedirectResponse(url="/dashboard", status_code=303)

    request.session["sync_local_subpath"] = local_subpath
    request.session["sync_remote_folder"] = remote_folder
    request.session["sync_include_globs"] = include_globs
    request.session["sync_exclude_globs"] = exclude_globs
    request.session["sync_conflict_behavior"] = conflict_behavior
    request.session["sync_force_rescan"] = bool(force_rescan)

    token_result = acquire_access_token_silent()
    if not token_result or "access_token" not in token_result:
        request.session["oauth_error"] = "Connect OneDrive before running a dry run."
        return RedirectResponse(url="/dashboard", status_code=303)

    try:
        plan = await build_sync_plan(
            access_token=token_result["access_token"],
            base_root=config.SYNC_LOCAL_BASE_ROOT,
            local_subpath=local_subpath,
            remote_folder=remote_folder,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
            conflict_behavior=conflict_behavior,
            force_rescan=bool(force_rescan),
        )
        request.session["last_dry_run"] = {
            "summary": plan["summary"],
            "local_root": plan["local_root"],
            "remote_folder": plan["remote_folder"],
            "conflict_behavior": conflict_behavior,
            "force_rescan": bool(force_rescan),
            "sample": plan["items"][:20],
        }
        request.session.pop("oauth_error", None)
    except Exception as exc:
        request.session["oauth_error"] = f"Dry run failed: {exc}"

    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/sync/start")
async def sync_start(
    request: Request,
    csrf_token: str = Form(...),
    local_subpath: str = Form(""),
    remote_folder: str = Form(""),
    include_globs: str = Form("*"),
    exclude_globs: str = Form("*.tmp,*.part,__pycache__/*,.git/*"),
    conflict_behavior: str = Form("replace"),
    force_rescan: str | None = Form(None),
):
    if not _validate_csrf_token(request, csrf_token):
        return RedirectResponse(url="/dashboard", status_code=303)

    request.session["sync_local_subpath"] = local_subpath
    request.session["sync_remote_folder"] = remote_folder
    request.session["sync_include_globs"] = include_globs
    request.session["sync_exclude_globs"] = exclude_globs
    request.session["sync_conflict_behavior"] = conflict_behavior
    request.session["sync_force_rescan"] = bool(force_rescan)

    token_result = acquire_access_token_silent()
    if not token_result or "access_token" not in token_result:
        request.session["oauth_error"] = "Connect OneDrive before starting sync."
        return RedirectResponse(url="/dashboard", status_code=303)

    try:
        plan = await build_sync_plan(
            access_token=token_result["access_token"],
            base_root=config.SYNC_LOCAL_BASE_ROOT,
            local_subpath=local_subpath,
            remote_folder=remote_folder,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
            conflict_behavior=conflict_behavior,
            force_rescan=bool(force_rescan),
        )
    except Exception as exc:
        request.session["oauth_error"] = f"Failed to build sync plan: {exc}"
        return RedirectResponse(url="/dashboard", status_code=303)

    job = create_job(
        local_subpath,
        remote_folder,
        include_globs,
        exclude_globs,
        conflict_behavior,
        force_rescan=bool(force_rescan),
    )
    update_job(
        job["id"],
        status="running",
        started_at=datetime.now(timezone.utc).isoformat(),
        progress={
            "files_total": plan["summary"]["to_upload"],
            "files_uploaded": 0,
            "files_skipped": plan["summary"]["unchanged"],
            "files_failed": 0,
            "bytes_total": plan["summary"]["bytes_to_upload"],
            "bytes_uploaded": 0,
        },
    )
    append_job_event(job["id"], "Job queued")
    append_job_event(job["id"], f"Conflict behavior: {conflict_behavior}")

    async def _progress_callback(
        files_uploaded: int,
        bytes_uploaded: int,
        files_failed: int | None = None,
    ) -> None:
        current = get_job(job["id"])
        if not current:
            return
        progress = current.get("progress", {})
        progress["files_uploaded"] = files_uploaded
        progress["bytes_uploaded"] = bytes_uploaded
        if files_failed is not None:
            progress["files_failed"] = files_failed
        update_job(job["id"], progress=progress)

    async def _event_callback(message: str) -> None:
        append_job_event(job["id"], message)

    async def _run_sync() -> None:
        try:
            await execute_sync_plan(
                access_token=token_result["access_token"],
                plan=plan,
                progress_callback=_progress_callback,
                event_callback=_event_callback,
                max_retries=max(0, config.SYNC_MAX_RETRIES),
            )
            update_job(
                job["id"],
                status="completed",
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            append_job_event(job["id"], "Job completed")
        except asyncio.CancelledError:
            update_job(
                job["id"],
                status="cancelled",
                finished_at=datetime.now(timezone.utc).isoformat(),
                error=None,
            )
            append_job_event(job["id"], "Job cancelled by user request")
            raise
        except Exception as exc:
            update_job(
                job["id"],
                status="failed",
                error=str(exc),
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            append_job_event(job["id"], f"Job failed: {exc}")
        finally:
            SYNC_TASKS.pop(job["id"], None)

    task = asyncio.create_task(_run_sync())
    SYNC_TASKS[job["id"]] = task
    request.session["last_sync_job_id"] = job["id"]
    request.session.pop("oauth_error", None)
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/sync/jobs/latest")
async def sync_latest_job():
    return get_latest_job() or {"status": "none"}


@app.post("/sync/jobs/{job_id}/cancel")
async def sync_cancel_job(request: Request, job_id: str, csrf_token: str = Form(...)):
    if not _validate_csrf_token(request, csrf_token):
        return RedirectResponse(url="/dashboard", status_code=303)

    job = get_job(job_id)
    if not job:
        return RedirectResponse(url="/sync/jobs", status_code=303)

    status = job.get("status")
    if status in {"completed", "failed", "cancelled"}:
        append_job_event(job_id, f"Cancel ignored: job already {status}")
        next_url = request.headers.get("referer") or "/sync/jobs"
        return RedirectResponse(url=next_url, status_code=303)

    task = SYNC_TASKS.get(job_id)
    if task and not task.done():
        update_job(job_id, status="cancelling")
        append_job_event(job_id, "Cancel requested")
        task.cancel()
    else:
        update_job(
            job_id,
            status="cancelled",
            finished_at=datetime.now(timezone.utc).isoformat(),
            error=None,
        )
        append_job_event(job_id, "Cancelled before task started")

    next_url = request.headers.get("referer") or "/sync/jobs"
    return RedirectResponse(url=next_url, status_code=303)


@app.get("/sync/jobs/{job_id}")
async def sync_job(job_id: str):
    job = get_job(job_id)
    if not job:
        return {"error": "job_not_found"}
    return job


@app.get("/health")
async def health():
    return {"status": "ok", "service": "onedrive-sync-manager"}
