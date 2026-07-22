import re
from collections import deque
from pathlib import Path

from .supervisor import SERVICES

_CHUNK_SIZE = 64 * 1024
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def tail_log(service_key: str, lines: int = 200) -> list[str]:
    spec = SERVICES.get(service_key)
    if spec is None:
        raise KeyError(service_key)
    if not spec.log_file.exists():
        return []
    return _tail_file(spec.log_file, lines)


def _tail_file(path: Path, lines: int) -> list[str]:
    with open(path, "rb") as f:
        f.seek(0, 2)
        file_size = f.tell()
        block = b""
        newline_count = 0
        pos = file_size

        while pos > 0 and newline_count <= lines:
            read_size = min(_CHUNK_SIZE, pos)
            pos -= read_size
            f.seek(pos)
            block = f.read(read_size) + block
            newline_count = block.count(b"\n")

        text = block.decode("utf-8", errors="replace")

    result = text.splitlines()
    return result[-lines:]


def log_file_path(service_key: str) -> Path:
    spec = SERVICES.get(service_key)
    if spec is None:
        raise KeyError(service_key)
    return spec.log_file


def search_log(
    service_key: str,
    query: str,
    *,
    regex: bool = False,
    case_sensitive: bool = False,
    context: int = 0,
    max_results: int = 500,
) -> dict:
    """Streams the log file line-by-line looking for matches (unlike
    tail_log's backward-chunk read, which is tail-specific). Returns
    {"matches": [{"line_no", "text", "context_before", "context_after"}],
    "truncated": bool}. Raises KeyError for an unknown service, ValueError
    for an invalid regex."""
    spec = SERVICES.get(service_key)
    if spec is None:
        raise KeyError(service_key)
    if not spec.log_file.exists():
        return {"matches": [], "truncated": False}

    if regex:
        try:
            pattern = re.compile(query, 0 if case_sensitive else re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"Invalid regular expression: {exc}") from exc

        def is_match(line: str) -> bool:
            return pattern.search(line) is not None
    else:
        needle = query if case_sensitive else query.lower()

        def is_match(line: str) -> bool:
            haystack = line if case_sensitive else line.lower()
            return needle in haystack

    matches = []
    truncated = False
    context_buffer: deque[tuple[int, str]] = deque(maxlen=context)
    # Matches still waiting to accumulate their `context` trailing lines.
    pending_after: list[dict] = []

    with open(spec.log_file, encoding="utf-8", errors="replace") as f:
        for line_no, raw_line in enumerate(f, start=1):
            line = _ANSI_RE.sub("", raw_line.rstrip("\n"))

            if pending_after:
                for m in pending_after:
                    m["context_after"].append((line_no, line))
                pending_after = [m for m in pending_after if len(m["context_after"]) < context]

            if is_match(line):
                if len(matches) >= max_results:
                    truncated = True
                    break
                m = {"line_no": line_no, "text": line, "context_before": list(context_buffer), "context_after": []}
                matches.append(m)
                if context:
                    pending_after.append(m)

            context_buffer.append((line_no, line))

    return {"matches": matches, "truncated": truncated}
