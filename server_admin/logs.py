from pathlib import Path

from .supervisor import SERVICES

_CHUNK_SIZE = 64 * 1024


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
