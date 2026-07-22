import httpx


def summarize_upstream_error(exc: httpx.HTTPError, service: str) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        message = f"{service} rejected the request (HTTP {exc.response.status_code})"
        try:
            body = exc.response.json()
            detail = body.get("detail") or body.get("error") or body.get("message")
        except (ValueError, AttributeError):
            detail = None
        if detail:
            message += f": {detail}"
        return message
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return f"Could not reach {service} — check that it's running."
    if isinstance(exc, httpx.TimeoutException):
        return f"{service} timed out responding."
    return f"{service} request failed: {exc}"
