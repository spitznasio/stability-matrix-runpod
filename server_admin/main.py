import asyncio
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from . import config, env_vars
from .formatting import format_bytes, format_rate, format_uptime, sparkline_points
from .logs import log_file_path, search_log, tail_log
from .severity import compute_health
from .supervisor import SERVICES, monitor_loop, service_manager
from .telemetry import gpu as gpu_telemetry
from .telemetry import history
from .telemetry.gpu import get_gpu_telemetry
from .telemetry.network import get_network_telemetry
from .telemetry.system import get_system_telemetry

LOGIN_EXEMPT_PATHS = {"/login", "/health"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    gpu_telemetry.init_nvml()
    sample_task = asyncio.create_task(history.sample_loop())
    crash_task = asyncio.create_task(monitor_loop())
    try:
        yield
    finally:
        for task in (sample_task, crash_task):
            task.cancel()
        for task in (sample_task, crash_task):
            try:
                await task
            except asyncio.CancelledError:
                pass
        gpu_telemetry.shutdown_nvml()


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
app.add_middleware(SessionMiddleware, secret_key=config.SESSION_SECRET, session_cookie="server_admin_session")

BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.filters["format_bytes"] = format_bytes
templates.env.filters["format_rate"] = format_rate
templates.env.filters["format_uptime"] = format_uptime
templates.env.filters["sparkline_points"] = sparkline_points


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def render_error(request: Request, message: str, status_code: int = 200) -> HTMLResponse:
    template = "_error.html" if is_htmx(request) else "error.html"
    return templates.TemplateResponse(request, template, {"message": message}, status_code=status_code)


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
    cpu_history = history.get_history("cpu_percent")
    mem_history = history.get_history("mem_percent")
    return templates.TemplateResponse(
        request,
        "_dashboard_telemetry.html",
        {"system": system, "cpu_history": cpu_history, "mem_history": mem_history},
    )


@app.get("/dashboard/gpu", response_class=HTMLResponse)
async def dashboard_gpu(request: Request):
    statuses = await run_in_threadpool(service_manager.all_statuses)
    pid_to_service = {status.pid: key for key, status in statuses.items() if status.pid is not None}
    gpu = get_gpu_telemetry(pid_to_service)
    gpu_history = {g["index"]: history.get_gpu_history(g["index"]) for g in gpu["gpus"]}
    return templates.TemplateResponse(
        request, "_dashboard_gpu.html", {"gpu": gpu, "gpu_history": gpu_history, "services": SERVICES}
    )


@app.get("/dashboard/network", response_class=HTMLResponse)
async def dashboard_network(request: Request):
    network = get_network_telemetry()
    send_history = history.get_history("net_send_bps")
    recv_history = history.get_history("net_recv_bps")
    return templates.TemplateResponse(
        request,
        "_dashboard_network.html",
        {"network": network, "send_history": send_history, "recv_history": recv_history},
    )


def _service_rows() -> list[dict]:
    rows = []
    for key, status in service_manager.all_statuses().items():
        spec = SERVICES[key]
        usage = service_manager.get(key).resource_usage(status.pid) if status.running and status.pid else None
        rows.append(
            {
                "key": key,
                "display_name": spec.display_name,
                "running": status.running,
                "pid": status.pid,
                "uptime_s": status.uptime_s,
                "crashed": status.crashed,
                "auto_restart": key in config.AUTO_RESTART_SERVICES,
                "cpu_percent": usage["cpu_percent"] if usage else None,
                "rss_mb": usage["rss_mb"] if usage else None,
            }
        )
    return rows


def _environment_row_context(spec: env_vars.EnvVarSpec, *, revealed: bool = False, editing: bool = False) -> dict:
    value = env_vars.current_value(spec.key)
    display_value = value if (not spec.sensitive or revealed) else env_vars.mask(value)
    return {
        "spec": spec,
        "value": value,
        "display_value": display_value,
        "revealed": revealed,
        "editing": editing,
        "has_override": env_vars.has_override(spec.key),
    }


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
async def logs_page(request: Request, service: str = "invokeai", lines: int = config.LOG_TAIL_LINES):
    lines = min(lines, config.MAX_LOG_TAIL_LINES)
    try:
        log_lines = tail_log(service, lines)
    except KeyError:
        return render_error(request, f"Unknown service: {service}", status_code=404)
    return templates.TemplateResponse(
        request,
        "logs.html",
        {"active_nav": "logs", "services": SERVICES, "selected": service, "lines": log_lines, "tail_lines": lines},
    )


@app.get("/logs/tail", response_class=HTMLResponse)
async def logs_tail(request: Request, service: str = "invokeai", lines: int = config.LOG_TAIL_LINES):
    lines = min(lines, config.MAX_LOG_TAIL_LINES)
    try:
        log_lines = tail_log(service, lines)
    except KeyError:
        return render_error(request, f"Unknown service: {service}", status_code=404)
    return templates.TemplateResponse(
        request, "_log_tail.html", {"lines": log_lines, "selected": service, "services": SERVICES}
    )


@app.get("/logs/search", response_class=HTMLResponse)
async def logs_search(
    request: Request,
    service: str = "invokeai",
    q: str = "",
    regex: bool = False,
    case_sensitive: bool = False,
    context: int = 0,
):
    if not q:
        return templates.TemplateResponse(request, "_log_search_results.html", {"result": None, "query": q})
    try:
        result = await run_in_threadpool(
            search_log, service, q, regex=regex, case_sensitive=case_sensitive, context=max(0, min(context, 10))
        )
    except KeyError:
        return render_error(request, f"Unknown service: {service}", status_code=404)
    except ValueError as exc:
        return render_error(request, str(exc), status_code=400)
    return templates.TemplateResponse(request, "_log_search_results.html", {"result": result, "query": q})


@app.get("/logs/download/{service_key}")
async def logs_download(request: Request, service_key: str):
    try:
        path = log_file_path(service_key)
    except KeyError:
        return render_error(request, f"Unknown service: {service_key}", status_code=404)
    if not path.exists():
        return render_error(request, f"No log file yet for service: {service_key}", status_code=404)
    return FileResponse(path, filename=f"{service_key}.log", media_type="text/plain")


@app.get("/environment", response_class=HTMLResponse)
async def environment_page(request: Request):
    return templates.TemplateResponse(request, "environment.html", {"active_nav": "environment"})


@app.get("/environment/list", response_class=HTMLResponse)
async def environment_list(request: Request):
    groups = [
        (category, [_environment_row_context(spec) for spec in specs]) for category, specs in env_vars.categories()
    ]
    return templates.TemplateResponse(request, "_environment_list.html", {"groups": groups})


@app.get("/environment/{key}/view", response_class=HTMLResponse)
async def environment_view(request: Request, key: str):
    try:
        spec = env_vars.get_spec(key)
    except KeyError:
        return render_error(request, f"Unknown environment variable: {key}", status_code=404)
    return templates.TemplateResponse(request, "_environment_row.html", {"row": _environment_row_context(spec)})


@app.get("/environment/{key}/reveal", response_class=HTMLResponse)
async def environment_reveal(request: Request, key: str):
    try:
        spec = env_vars.get_spec(key)
    except KeyError:
        return render_error(request, f"Unknown environment variable: {key}", status_code=404)
    return templates.TemplateResponse(
        request, "_environment_row.html", {"row": _environment_row_context(spec, revealed=True)}
    )


@app.get("/environment/{key}/edit", response_class=HTMLResponse)
async def environment_edit(request: Request, key: str):
    try:
        spec = env_vars.get_spec(key)
    except KeyError:
        return render_error(request, f"Unknown environment variable: {key}", status_code=404)
    return templates.TemplateResponse(
        request, "_environment_row.html", {"row": _environment_row_context(spec, editing=True)}
    )


@app.post("/environment/{key}", response_class=HTMLResponse)
async def environment_save(request: Request, key: str, value: str = Form("")):
    try:
        spec = env_vars.get_spec(key)
    except KeyError:
        return render_error(request, f"Unknown environment variable: {key}", status_code=404)
    skip_write = spec.sensitive and not value and bool(env_vars.current_value(key))
    if not skip_write:
        try:
            await run_in_threadpool(env_vars.set_value, key, value)
        except OSError as exc:
            message = f"Applied in memory, but failed to save to disk: {exc}"
            if is_htmx(request):
                import html
                return HTMLResponse(
                    f'<tr><td colspan="5"><p class="error-banner">{html.escape(message)}</p></td></tr>',
                    status_code=500,
                )
            return render_error(request, message, status_code=500)
    return templates.TemplateResponse(request, "_environment_row.html", {"row": _environment_row_context(spec)})


@app.post("/environment/{key}/clear", response_class=HTMLResponse)
async def environment_clear(request: Request, key: str):
    try:
        spec = env_vars.get_spec(key)
    except KeyError:
        return render_error(request, f"Unknown environment variable: {key}", status_code=404)
    try:
        await run_in_threadpool(env_vars.clear_value, key)
    except OSError as exc:
        message = f"Applied in memory, but failed to save to disk: {exc}"
        if is_htmx(request):
            import html
            return HTMLResponse(
                f'<tr><td colspan="5"><p class="error-banner">{html.escape(message)}</p></td></tr>',
                status_code=500,
            )
        return render_error(request, message, status_code=500)
    return templates.TemplateResponse(request, "_environment_row.html", {"row": _environment_row_context(spec)})
