import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from . import config
from .aria2_client import Aria2Client
from .aria2_client import TERMINAL_STATUSES as ARIA2_TERMINAL_STATUSES
from .civitai_client import CivitAIClient
from .formatting import format_commercial_use
from .invokeai_client import InvokeAIClient

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
    }
    template = "browse_results.html" if is_htmx(request) else "browse.html"
    return templates.TemplateResponse(request, template, context)


@app.get("/models/{model_id}", response_class=HTMLResponse)
async def model_detail(request: Request, model_id: int, refresh: bool = False):
    model = await request.app.state.civitai.get_model(model_id, refresh=refresh)
    model = {**model, "allowCommercialUse_display": format_commercial_use(model.get("allowCommercialUse"))}

    return templates.TemplateResponse(
        request, "model_detail.html", {"model": model, "active_nav": "browse"}
    )


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
async def install(request: Request, download_url: str = Form(...)):
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
    request: Request, download_url: str = Form(...), filename: str = Form(...), sha256: str = Form("")
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
    return templates.TemplateResponse(
        request,
        "_download_status.html",
        {"job": job, "terminal": job.get("status") in ARIA2_TERMINAL_STATUSES},
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
