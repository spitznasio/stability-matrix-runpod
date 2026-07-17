def format_bytes(num_bytes: float) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{value:.0f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def format_rate(bytes_per_sec: float) -> str:
    return f"{format_bytes(bytes_per_sec)}/s"


SPARKLINE_WIDTH = 120
SPARKLINE_HEIGHT = 32


def sparkline_points(history: list[tuple[float, float]]) -> str:
    """Convert a [(timestamp, value), ...] series into an SVG polyline
    `points` string, auto-scaled to fill a SPARKLINE_WIDTH x SPARKLINE_HEIGHT
    viewBox (y inverted, since SVG y grows downward)."""
    if len(history) < 2:
        return ""

    times = [t for t, _ in history]
    values = [v for _, v in history]
    t_min, t_max = times[0], times[-1]
    v_min, v_max = min(values), max(values)
    t_span = t_max - t_min or 1.0
    v_span = v_max - v_min or 1.0

    points = []
    for t, v in history:
        x = (t - t_min) / t_span * SPARKLINE_WIDTH
        y = SPARKLINE_HEIGHT - (v - v_min) / v_span * SPARKLINE_HEIGHT
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)


def format_uptime(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"
