import secrets
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from . import config
from .formatting import format_bytes, format_rate, format_uptime
from .logs import tail_log
from .supervisor import SERVICES, service_manager
from .telemetry.gpu import get_gpu_telemetry
from .telemetry.network import get_network_telemetry
from .telemetry.system import get_system_telemetry

LOGIN_EXEMPT_PATHS = {"/login", "/health"}


class SessionAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not (config.AUTH_USERNAME and config.AUTH_PASSWORD):
            return await call_next(request)
        path = request.url.path
        if path in LOGIN_EXEMPT_PATHS or path.startswith("/static/") or request.session.get("authenticated"):
            return await call_next(request)
        return RedirectResponse(url=f"/login?next={path}")


app = FastAPI()
app.add_middleware(SessionAuthMiddleware)
# Added last so it runs first (outermost), making request.session available
# to SessionAuthMiddleware further down the stack.
app.add_middleware(SessionMiddleware, secret_key=config.SESSION_SECRET, session_cookie="server_admin_session")

BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.filters["format_bytes"] = format_bytes
templates.env.filters["format_rate"] = format_rate
templates.env.filters["format_uptime"] = format_uptime


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def render_error(request: Request, message: str, status_code: int = 200) -> HTMLResponse:
    template = "_error.html" if is_htmx(request) else "error.html"
    return templates.TemplateResponse(request, template, {"message": message}, status_code=status_code)


def _percent_severity(percent: float) -> str:
    if percent >= 90:
        return "danger"
    if percent >= 70:
        return "warn"
    return "ok"


def compute_health(system: dict, gpu: dict) -> str:
    severities = [
        _percent_severity(system["cpu_percent"]),
        _percent_severity(system["mem_percent"]),
        _percent_severity(system["disk_percent"]),
    ]
    if gpu["available"]:
        for g in gpu["gpus"]:
            severities.append(_percent_severity(g["utilization_gpu"]))
            severities.append(_percent_severity(g["temperature_c"]))
    if "danger" in severities:
        return "danger"
    if "warn" in severities:
        return "warn"
    return "ok"


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, next: str = "/dashboard"):
    return templates.TemplateResponse(request, "login.html", {"next": next, "error": None})


@app.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/dashboard")
):
    next = next if next.startswith("/") else "/dashboard"
    if (
        config.AUTH_USERNAME
        and config.AUTH_PASSWORD
        and secrets.compare_digest(username, config.AUTH_USERNAME)
        and secrets.compare_digest(password, config.AUTH_PASSWORD)
    ):
        request.session["authenticated"] = True
        return RedirectResponse(url=next, status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"next": next, "error": "Invalid username or password."}, status_code=401
    )


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def index():
    return RedirectResponse(url="/dashboard")


@app.get("/status-strip", response_class=HTMLResponse)
async def status_strip(request: Request):
    system = await run_in_threadpool(get_system_telemetry)
    gpu = get_gpu_telemetry()
    health = compute_health(system, gpu)
    return templates.TemplateResponse(request, "_status_strip.html", {"health": health})


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {"active_nav": "dashboard"})


@app.get("/dashboard/telemetry", response_class=HTMLResponse)
async def dashboard_telemetry(request: Request):
    system = await run_in_threadpool(get_system_telemetry)
    return templates.TemplateResponse(request, "_dashboard_telemetry.html", {"system": system})


@app.get("/dashboard/gpu", response_class=HTMLResponse)
async def dashboard_gpu(request: Request):
    gpu = get_gpu_telemetry()
    return templates.TemplateResponse(request, "_dashboard_gpu.html", {"gpu": gpu})


@app.get("/dashboard/network", response_class=HTMLResponse)
async def dashboard_network(request: Request):
    network = get_network_telemetry()
    return templates.TemplateResponse(request, "_dashboard_network.html", {"network": network})


def _service_rows() -> list[dict]:
    rows = []
    for key, status in service_manager.all_statuses().items():
        spec = SERVICES[key]
        rows.append(
            {
                "key": key,
                "display_name": spec.display_name,
                "running": status.running,
                "pid": status.pid,
                "uptime_s": status.uptime_s,
            }
        )
    return rows


@app.get("/services", response_class=HTMLResponse)
async def services(request: Request):
    return templates.TemplateResponse(request, "services.html", {"active_nav": "services"})


@app.get("/services/list", response_class=HTMLResponse)
async def services_list(request: Request):
    return templates.TemplateResponse(request, "_services_list.html", {"services": _service_rows()})


@app.post("/services/{key}/{action}", response_class=HTMLResponse)
async def services_control(request: Request, key: str, action: str):
    if action not in {"start", "stop", "restart"}:
        return render_error(request, f"Unknown action: {action}", status_code=400)
    try:
        svc = service_manager.get(key)
    except KeyError:
        return render_error(request, f"Unknown service: {key}", status_code=404)

    await run_in_threadpool(getattr(svc, action))
    return templates.TemplateResponse(request, "_services_list.html", {"services": _service_rows()})


@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request, service: str = "invokeai"):
    lines = tail_log(service, config.LOG_TAIL_LINES)
    return templates.TemplateResponse(
        request,
        "logs.html",
        {"active_nav": "logs", "services": SERVICES, "selected": service, "lines": lines},
    )


@app.get("/logs/tail", response_class=HTMLResponse)
async def logs_tail(request: Request, service: str = "invokeai"):
    try:
        lines = tail_log(service, config.LOG_TAIL_LINES)
    except KeyError:
        return render_error(request, f"Unknown service: {service}", status_code=404)
    return templates.TemplateResponse(
        request, "_log_tail.html", {"lines": lines, "selected": service, "services": SERVICES}
    )
