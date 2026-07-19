import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlencode

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from . import config, downloads, metadata_store
from .aria2_client import Aria2Client
from .aria2_client import TERMINAL_STATUSES as ARIA2_TERMINAL_STATUSES
from .civitai_client import CivitAIClient
from .formatting import format_commercial_use
from .invokeai_client import InvokeAIClient
from .sanitize import html_to_text

# basicConfig here (not in a __main__ guard) because uvicorn imports this
# module directly rather than running it as a script — this is the only
# place the process-wide root logger gets configured. Output goes to
# stdout/stderr, which the Server Admin supervisor already redirects to
# /tmp/server-admin/logs/civitai-manager.log.
logging.basicConfig(level=config.LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

LOGIN_EXEMPT_PATHS = {"/login", "/health"}

TERMINAL_STATUSES = {"completed", "error", "cancelled"}

# CivitAI has no published enum for this — curated to the base models that
# actually show up most often in search results today.
BASE_MODEL_CHOICES = [
    "SD 1.5",
    "SDXL 1.0",
    "Pony",
    "Illustrious",
    "NoobAI",
    "Anima",
    "Flux.1 D",
    "SD 3.5",
    "Other",
]

INSTALL_METADATA_POLL_SECONDS = 2.0
INSTALL_METADATA_MAX_POLL_SECONDS = 1800.0  # give up after 30 minutes of polling

# asyncio.create_task() does not keep a strong reference to the task itself —
# if nothing else holds one, the task can be garbage-collected mid-await
# (e.g. during the poll loop's asyncio.sleep), silently aborting it. Keep
# background install-tracking tasks alive here until they finish.
_background_install_tasks: set[asyncio.Task] = set()


def _build_sidecar_metadata(model: dict, version_id: int) -> dict:
    version = next((v for v in model.get("modelVersions", []) if v.get("id") == version_id), None)
    creator = model.get("creator") or {}
    return {
        "civitai_model_id": model.get("id"),
        "civitai_version_id": version_id,
        "civitai_url": f"https://civitai.com/models/{model.get('id')}",
        "model_name": model.get("name"),
        "type": model.get("type"),
        "base_model": version.get("baseModel") if version else None,
        "creator_username": creator.get("username"),
        "description": model.get("description"),
        "trigger_words": (version.get("trainedWords") if version else None) or [],
        "tags": [t.get("name") if isinstance(t, dict) else t for t in (model.get("tags") or [])],
        "stats": model.get("stats"),
        "allowCommercialUse": model.get("allowCommercialUse"),
        "allowDerivatives": model.get("allowDerivatives"),
        "nsfw": model.get("nsfw"),
        "publishedAt": model.get("publishedAt"),
        "versions": [
            {
                "id": v.get("id"),
                "name": v.get("name"),
                "images": (v.get("images") or [])[:1],
            }
            for v in model.get("modelVersions", [])
        ],
        "installed_version_id": version_id,
        "installed_version_name": version.get("name") if version else None,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


def _extract_installed_path(job: dict) -> str | None:
    # Confirmed against a live pod: a completed ModelInstallJob's config_out.path
    # holds the installed model's on-disk path for local-source installs (a
    # top-level "local_path" field carries the same value). The other
    # fallbacks below are kept for payload shapes not yet observed directly.
    config_out = job.get("config") if isinstance(job.get("config"), dict) else None
    if config_out and config_out.get("path"):
        return config_out["path"]
    config_out2 = job.get("config_out") if isinstance(job.get("config_out"), dict) else None
    if config_out2 and config_out2.get("path"):
        return config_out2["path"]
    if job.get("path"):
        return job["path"]
    return None


async def _track_install_metadata(app: FastAPI, job_id: str, model_id: int, version_id: int) -> None:
    # Runs independently of any client connection so metadata capture doesn't
    # depend on a browser tab staying open/visible for the entire install —
    # see docs/superpowers/specs/2026-07-19-installed-page-mirror-design.md,
    # "Server-side install-completion tracking".
    invokeai: InvokeAIClient = app.state.invokeai
    civitai: CivitAIClient = app.state.civitai
    max_attempts = int(INSTALL_METADATA_MAX_POLL_SECONDS / INSTALL_METADATA_POLL_SECONDS)
    for _ in range(max_attempts):
        await asyncio.sleep(INSTALL_METADATA_POLL_SECONDS)
        try:
            job = await invokeai.get_install_job(job_id)
        except httpx.HTTPError:
            logger.warning(
                "Lost contact with InvokeAI tracking install job %s for metadata capture",
                job_id, exc_info=True,
            )
            return
        if job.get("status") not in TERMINAL_STATUSES:
            continue
        if job.get("status") != "completed":
            logger.info(
                "Install job %s did not complete successfully (status=%s); skipping metadata capture",
                job_id, job.get("status"),
            )
            return
        installed_path = _extract_installed_path(job)
        if not installed_path:
            logger.warning(
                "Install job %s completed but no installed path found in job payload; "
                "skipping metadata capture. job=%s", job_id, job,
            )
            return
        try:
            model = await civitai.get_model(model_id)
        except httpx.HTTPError:
            logger.warning(
                "Failed to fetch CivitAI model %s for metadata capture after install",
                model_id, exc_info=True,
            )
            return
        metadata_store.write_sidecar(installed_path, _build_sidecar_metadata(model, version_id))
        logger.info(
            "Captured install metadata for model_id=%s version_id=%s at %s",
            model_id, version_id, installed_path,
        )
        return
    logger.warning(
        "Install job %s did not reach a terminal status within %s seconds; giving up on metadata capture",
        job_id, INSTALL_METADATA_MAX_POLL_SECONDS,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("civitai_manager starting up")
    app.state.civitai = CivitAIClient()
    app.state.invokeai = InvokeAIClient()
    app.state.aria2 = Aria2Client()
    try:
        yield
    finally:
        await app.state.civitai.aclose()
        await app.state.invokeai.aclose()
        await app.state.aria2.aclose()
        logger.info("civitai_manager shutting down")


class SessionAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not (config.AUTH_USERNAME and config.AUTH_PASSWORD):
            return await call_next(request)
        path = request.url.path
        if path in LOGIN_EXEMPT_PATHS or path.startswith("/static/") or request.session.get("authenticated"):
            return await call_next(request)
        return RedirectResponse(url=f"/login?next={path}")


app = FastAPI(lifespan=lifespan)
app.add_middleware(SessionAuthMiddleware)
# Added last so it runs first (outermost), making request.session available
# to SessionAuthMiddleware further down the stack.
app.add_middleware(SessionMiddleware, secret_key=config.SESSION_SECRET, session_cookie="civitai_manager_session")
BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, next: str = "/browse"):
    return templates.TemplateResponse(request, "login.html", {"next": next, "error": None})


@app.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/browse")
):
    next = next if next.startswith("/") else "/browse"
    if (
        config.AUTH_USERNAME
        and config.AUTH_PASSWORD
        and secrets.compare_digest(username, config.AUTH_USERNAME)
        and secrets.compare_digest(password, config.AUTH_PASSWORD)
    ):
        request.session["authenticated"] = True
        logger.info("Login succeeded for user %r", username)
        return RedirectResponse(url=next, status_code=303)
    logger.warning("Login failed for user %r", username)
    return templates.TemplateResponse(
        request, "login.html", {"next": next, "error": "Invalid username or password."}, status_code=401
    )


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login")


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def render_error(request: Request, message: str, status_code: int = 200) -> HTMLResponse:
    template = "_error.html" if is_htmx(request) else "error.html"
    return templates.TemplateResponse(
        request, template, {"message": message}, status_code=status_code
    )


@app.exception_handler(httpx.HTTPError)
async def httpx_error_handler(request: Request, exc: httpx.HTTPError) -> HTMLResponse:
    # This is the only place the real exception is visible — the generic
    # user-facing message alone can't tell you which upstream call failed or
    # why. Logging it here means the log viewer shows the actual cause
    # instead of requiring a manual curl against InvokeAI/aria2 to diagnose.
    logger.exception("Unhandled upstream error on %s %s", request.method, request.url.path, exc_info=exc)
    return render_error(request, f"Upstream request failed: {exc}", status_code=502)


@app.get("/")
async def index():
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/browse")


@app.get("/browse", response_class=HTMLResponse)
async def browse(
    request: Request,
    q: str = "",
    types: str = "",
    base_model: str = "",
    sort: str = "Most Downloaded",
    period: str = "AllTime",
    # CivitAI excludes NSFW-flagged models unless `nsfw=true` is explicitly
    # sent — defaulting to "true" here so listings include NSFW out of the box.
    nsfw: str = "true",
    cursor: str = "",
    prev: str = "",
    refresh: bool = False,
):
    # CivitAI's search API only supports cursor-based pagination (a `page`
    # number can't be combined with a text `query`), so "Prev" is implemented
    # by keeping a stack of visited cursors in the `prev` query param.
    type_list = [t for t in types.split(",") if t]
    base_model_list = [base_model] if base_model else None
    nsfw_bool = nsfw != "false"
    results = await request.app.state.civitai.search_models(
        query=q,
        types=type_list,
        base_models=base_model_list,
        sort=sort,
        period=period,
        nsfw=nsfw_bool,
        cursor=cursor,
        refresh=refresh,
    )
    # "_root_" is a sentinel for "the first page" (cursor=""), since an empty
    # string can't be told apart from "no entry" once joined into the `prev`
    # query param's comma-separated stack.
    prev_stack = [c for c in prev.split(",") if c]
    prev_target = prev_stack[-1] if prev_stack else ""
    # Carried through model card links so "Back to Browse" restores this exact
    # search/filter/page state instead of resetting to a blank first page.
    return_to = quote(
        urlencode({
            "q": q, "types": types, "base_model": base_model, "sort": sort,
            "period": period, "nsfw": nsfw, "cursor": cursor, "prev": prev,
        }),
        safe="",
    )
    context = {
        "request": request,
        "active_nav": "browse",
        "models": results.get("items", results.get("models", [])),
        "metadata": results.get("metadata", {}),
        "q": q,
        "types": types,
        "base_model": base_model,
        "base_model_choices": BASE_MODEL_CHOICES,
        "sort": sort,
        "period": period,
        "nsfw": nsfw,
        "cursor": cursor,
        "has_prev": bool(prev_stack),
        "prev_cursor": "" if prev_target == "_root_" else prev_target,
        "prev_param": ",".join(prev_stack[:-1]),
        "next_prev_param": ",".join([*prev_stack, cursor or "_root_"]),
        "return_to": return_to,
    }
    template = "browse_results.html" if is_htmx(request) else "browse.html"
    return templates.TemplateResponse(request, template, context)


@app.get("/models/{model_id}", response_class=HTMLResponse)
async def model_detail(
    request: Request,
    model_id: int,
    refresh: bool = False,
    return_to: str = "",
    version: int = 0,
):
    model = await request.app.state.civitai.get_model(model_id, refresh=refresh)
    model = {**model, "allowCommercialUse_display": format_commercial_use(model.get("allowCommercialUse"))}

    back_url = f"/browse?{unquote(return_to)}" if return_to else "/browse"
    versions = model.get("modelVersions", [])
    active_version = next((v for v in versions if v["id"] == version), versions[0] if versions else None)

    context = {
        "request": request,
        "model": model,
        "active_nav": "browse",
        "back_url": back_url,
        "return_to": return_to,
        "versions": versions,
        "active_version": active_version,
        "civitai_url": f"https://civitai.com/models/{model_id}",
    }
    # Version tab clicks hx-get this same route — the partial swaps the version
    # body via hx-target/hx-select and relocates the sidebar install panel via
    # an out-of-band swap, both driven from the one response.
    template = "_version_update.html" if is_htmx(request) else "model_detail.html"
    return templates.TemplateResponse(request, template, context)


@app.get("/models/{model_id}/versions/{version_id}/gallery", response_class=HTMLResponse)
async def version_gallery(request: Request, model_id: int, version_id: int, refresh: bool = False):
    # Lazily enriches a version's thumbnails with generation metadata (prompt,
    # sampler, etc.) — only fetched once a version is actually expanded, since
    # fetching this for every version up front doesn't scale to models with
    # dozens of versions (one extra request per version).
    try:
        images = await request.app.state.civitai.get_version_images(version_id, refresh=refresh)
    except httpx.HTTPError:
        images = []
    return templates.TemplateResponse(request, "_gallery.html", {"images": images})


@app.post("/install", response_class=HTMLResponse)
async def install(
    request: Request,
    download_url: str = Form(...),
    model_id: str = Form(""),
    version_id: str = Form(""),
):
    logger.info("Install requested: %s", download_url)
    try:
        job = await request.app.state.invokeai.install_model(
            download_url, config.CIVITAI_API_TOKEN
        )
    except httpx.HTTPError:
        logger.warning("Install request rejected by InvokeAI for %s", download_url, exc_info=True)
        return render_error(
            request,
            "InvokeAI is not ready yet, or the install request was rejected — try again shortly.",
        )
    logger.info("Install job %s started for %s (status=%s)", job.get("id"), download_url, job.get("status"))
    if job.get("id") and model_id and version_id:
        task = asyncio.create_task(
            _track_install_metadata(request.app, job["id"], int(model_id), int(version_id))
        )
        _background_install_tasks.add(task)
        task.add_done_callback(_background_install_tasks.discard)
    return templates.TemplateResponse(
        request,
        "_install_status.html",
        {"job": job, "terminal": job.get("status") in TERMINAL_STATUSES},
    )


@app.get("/install/{job_id}/status", response_class=HTMLResponse)
async def install_status(request: Request, job_id: str):
    try:
        job = await request.app.state.invokeai.get_install_job(job_id)
    except httpx.HTTPError:
        logger.warning("Lost contact with InvokeAI polling install job %s", job_id, exc_info=True)
        return render_error(request, "Lost contact with InvokeAI while checking install status.")
    if job.get("status") in TERMINAL_STATUSES:
        logger.info("Install job %s reached terminal status %s", job_id, job.get("status"))
    return templates.TemplateResponse(
        request,
        "_install_status.html",
        {"job": job, "terminal": job.get("status") in TERMINAL_STATUSES},
    )


@app.post("/download", response_class=HTMLResponse)
async def download(
    request: Request,
    download_url: str = Form(...),
    filename: str = Form(...),
    sha256: str = Form(""),
    model_id: str = Form(""),
    model_name: str = Form(""),
    model_type: str = Form(""),
    version_id: str = Form(""),
    base_model: str = Form(""),
    civitai_url: str = Form(""),
    description: str = Form(""),
    trigger_words: str = Form(""),
):
    logger.info("Download-to-folder requested: %s -> %s", download_url, filename)
    try:
        gid = await request.app.state.aria2.add_download(download_url, filename, sha256 or None)
        job = await request.app.state.aria2.tell_status(gid)
    except httpx.HTTPError:
        logger.warning("aria2 daemon unreachable queueing download for %s", filename, exc_info=True)
        return render_error(
            request,
            "The download daemon is not reachable right now — try again shortly.",
        )
    # aria2 sanitizes `filename` down to a bare basename before writing the
    # file (see aria2_client._sanitize_filename) — the sidecar must be keyed
    # off the actual on-disk name reported in the job, or it won't be found
    # by list_downloaded_files() later. Fall back to a `.name`-only version of
    # the client-supplied `filename` (never the raw form value) so a missing
    # `files` entry in the aria2 response can't be used to write a sidecar
    # outside CIVITAI_DOWNLOAD_DIR via a "../"-laden filename.
    actual_names = [Path(f["path"]).name for f in job.get("files", [])]
    actual_name = actual_names[0] if actual_names else Path(filename).name
    # civitai_url is echoed back from a hidden form field, which any client
    # can freely edit before submitting — validate the scheme before it's
    # persisted and later rendered as an <a href> on the Downloads page.
    if civitai_url and not civitai_url.startswith(("http://", "https://")):
        civitai_url = ""
    trigger_words_list = [w for w in (w.strip() for w in trigger_words.split(",")) if w]
    metadata = {
        "model_id": model_id or None,
        "model_name": model_name or None,
        "model_type": model_type or None,
        "version_id": version_id or None,
        "base_model": base_model or None,
        "civitai_url": civitai_url or None,
        "description": description or None,
        "trigger_words": trigger_words_list,
        "sha256": sha256 or None,
    }
    metadata = {k: v for k, v in metadata.items() if v is not None}
    sidecar_target = (Path(config.CIVITAI_DOWNLOAD_DIR) / actual_name).resolve()
    download_dir = Path(config.CIVITAI_DOWNLOAD_DIR).resolve()
    logger.debug("Download /download: received trigger_words=%r, parsed to=%s, final metadata=%s", trigger_words, trigger_words_list, metadata)
    if metadata and sidecar_target.parent == download_dir:
        downloads.write_sidecar(sidecar_target, metadata)
        logger.debug("Sidecar written to %s with keys: %s", sidecar_target, list(metadata.keys()))
    if job.get("status") in ARIA2_TERMINAL_STATUSES:
        await request.app.state.aria2.cleanup_control_file(gid)
    logger.info("Download gid=%s queued for %s (status=%s)", gid, filename, job.get("status"))
    return templates.TemplateResponse(
        request,
        "_download_status.html",
        {"job": job, "terminal": job.get("status") in ARIA2_TERMINAL_STATUSES},
    )


@app.get("/download/{gid}/status", response_class=HTMLResponse)
async def download_status(request: Request, gid: str):
    try:
        job = await request.app.state.aria2.tell_status(gid)
    except httpx.HTTPError:
        logger.warning("Lost contact with aria2 polling gid=%s", gid, exc_info=True)
        return render_error(request, "Lost contact with the download daemon while checking status.")
    if job.get("status") in ARIA2_TERMINAL_STATUSES:
        logger.info("Download gid=%s reached terminal status %s", gid, job.get("status"))
        await request.app.state.aria2.cleanup_control_file(gid)
    return templates.TemplateResponse(
        request,
        "_download_status.html",
        {"job": job, "terminal": job.get("status") in ARIA2_TERMINAL_STATUSES},
    )


@app.get("/downloads", response_class=HTMLResponse)
async def downloads_list(request: Request):
    files = downloads.list_downloaded_files(Path(config.CIVITAI_DOWNLOAD_DIR))
    try:
        installed_models = await request.app.state.invokeai.list_models()
        installed_paths = {
            str(Path(m["path"]).resolve()) for m in installed_models if m.get("path")
        }
        invokeai_error = None
    except httpx.HTTPError:
        installed_paths = set()
        invokeai_error = "InvokeAI is not reachable right now — install status is unknown."
    for f in files:
        f["installed"] = str(f["path"].resolve()) in installed_paths
    return templates.TemplateResponse(
        request,
        "downloads.html",
        {"files": files, "error": invokeai_error, "active_nav": "downloads"},
    )


@app.post("/downloads/{filename}/install", response_class=HTMLResponse)
async def downloads_install(request: Request, filename: str):
    download_dir = Path(config.CIVITAI_DOWNLOAD_DIR).resolve()
    target = (download_dir / filename).resolve()
    if download_dir not in target.parents or not target.is_file():
        return render_error(request, "That file could not be found.", status_code=404)

    metadata = downloads.read_sidecar(target) or {}
    logger.debug("Install /downloads/{filename}/install: read sidecar metadata=%s", metadata)
    install_config = {
        "name": metadata.get("model_name"),
        "description": html_to_text(metadata.get("description")),
        "trigger_phrases": metadata.get("trigger_words"),
        "source_url": metadata.get("civitai_url"),
    }
    install_config = {k: v for k, v in install_config.items() if v is not None}
    logger.debug("Install built config (after filtering)=%s", install_config)

    logger.info("Install-from-download requested: %s", target)
    try:
        job = await request.app.state.invokeai.install_model(
            str(target), config.CIVITAI_API_TOKEN, inplace=True, config=install_config
        )
    except httpx.HTTPError:
        logger.warning("Install request rejected by InvokeAI for %s", target, exc_info=True)
        return render_error(
            request,
            "InvokeAI is not ready yet, or the install request was rejected — try again shortly.",
        )
    logger.info("Install job %s started for %s (status=%s)", job.get("id"), target, job.get("status"))
    return templates.TemplateResponse(
        request,
        "_install_status.html",
        {"job": job, "terminal": job.get("status") in TERMINAL_STATUSES},
    )


@app.get("/installed", response_class=HTMLResponse)
async def installed(request: Request):
    try:
        models = await request.app.state.invokeai.list_models()
    except httpx.HTTPError:
        return templates.TemplateResponse(
            request,
            "installed.html",
            {"models": [], "error": "InvokeAI is not reachable right now.", "active_nav": "installed"},
        )
    return templates.TemplateResponse(
        request,
        "installed.html",
        {"models": models, "error": None, "active_nav": "installed"},
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
