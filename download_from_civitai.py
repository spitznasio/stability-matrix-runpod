#!/usr/bin/env python3
"""Download files from Civitai using aria2c and a bearer token.

Examples:
  # Use a full Civitai download URL
  python download_from_civitai.py \
    --url "https://civitai.red/api/download/models/2786084?type=Model&format=SafeTensor&size=pruned&fp=fp16" \
    --dir /workspace/invokeai/models

  # Build the URL from model id + options
  python download_from_civitai.py \
    --model-id 2786084 \
    --type Model \
    --format SafeTensor \
    --size pruned \
    --fp fp16 \
        --civitai-api-token "your_token_here" \
    --dir /workspace/invokeai/models

    # Download a batch of entries from a text file
    python download_from_civitai.py \
        --list-file /workspace/civitai_models.txt \
        --civitai-api-token "your_token_here" \
        --dir /workspace/invokeai/models

    # Force re-download even if files already exist
    python download_from_civitai.py \
        --list-file /workspace/civitai_models.txt \
        --civitai-api-token "your_token_here" \
        --dir /workspace/invokeai/models \
        --force

List file format (one entry per line):
    - Full URL lines:
            https://civitai.red/api/download/models/2786084?type=Model&format=SafeTensor
    - Model ID lines with optional query overrides:
            2786084
            2786084?type=Model&format=SafeTensor&size=pruned&fp=fp16
    - Blank lines and lines starting with # are ignored.

Authentication:
    Reads the token from CIVITAI_API_TOKEN by default,
    or use --civitai-api-token/--token.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import shlex
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse
from urllib.request import Request, urlopen


DEFAULT_DOWNLOAD_DIR = "/workspace/invokeai/models"
DEFAULT_BASE_URL = "https://civitai.red"
ALLOWED_CIVITAI_HOSTS = {"civitai.red", "www.civitai.red"}


def validate_civitai_url(download_url: str, allowed_hosts: set[str] | None = None) -> str | None:
    """Return None if URL is acceptable for token-authenticated requests, else an error."""
    parsed = urlparse(download_url)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()

    if scheme != "https":
        return "URL must use https"
    if not host:
        return "URL must include a hostname"

    effective_allowed = allowed_hosts if allowed_hosts is not None else ALLOWED_CIVITAI_HOSTS
    if host in effective_allowed:
        return None
    if any(host.endswith(f".{allowed}") for allowed in effective_allowed):
        return None
    return f"URL host '{host}' is not in the allowlist"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download Civitai models with aria2c using CIVITAI_API_TOKEN "
            "or a CLI token argument."
        )
    )

    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--url",
        help="Full Civitai download URL (example: https://civitai.red/api/download/models/<id>?...)",
    )
    source.add_argument(
        "--model-id",
        type=int,
        help="Civitai model version id used in /api/download/models/<id>",
    )
    source.add_argument(
        "--list-file",
        default=None,
        help="Text file containing multiple model entries (URLs or model-id lines).",
    )

    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Civitai base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--type",
        default="Model",
        help="Query parameter: type (default: Model)",
    )
    parser.add_argument(
        "--format",
        default="SafeTensor",
        help="Query parameter: format (default: SafeTensor)",
    )
    parser.add_argument(
        "--size",
        default="pruned",
        help="Query parameter: size (default: pruned)",
    )
    parser.add_argument(
        "--fp",
        default="fp16",
        help="Query parameter: fp (default: fp16)",
    )
    parser.add_argument(
        "--civitai-api-token",
        "--token",
        dest="token",
        default=None,
        help=(
            "Bearer token. If omitted, uses CIVITAI_API_TOKEN env var. "
            "--token is kept as a short alias."
        ),
    )
    parser.add_argument(
        "--dir",
        default=DEFAULT_DOWNLOAD_DIR,
        help=f"Download directory (default: {DEFAULT_DOWNLOAD_DIR})",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Optional output filename. If omitted, aria2c uses server-provided filename.",
    )
    parser.add_argument(
        "--connections",
        type=int,
        default=16,
        help="Number of parallel connections for aria2c (-x and -s). Default: 16",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the command without running aria2c.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-download and overwrite existing files.",
    )

    return parser.parse_args()


def build_download_url(args: argparse.Namespace) -> str:
    if args.url:
        return args.url

    assert args.model_id is not None
    base = args.base_url.rstrip("/")
    params = {
        "type": args.type,
        "format": args.format,
        "size": args.size,
        "fp": args.fp,
    }
    return f"{base}/api/download/models/{args.model_id}?{urlencode(params)}"


def build_download_url_from_values(
    base_url: str,
    model_id: int,
    type_value: str,
    format_value: str,
    size_value: str,
    fp_value: str,
) -> str:
    base = base_url.rstrip("/")
    params = {
        "type": type_value,
        "format": format_value,
        "size": size_value,
        "fp": fp_value,
    }
    return f"{base}/api/download/models/{model_id}?{urlencode(params)}"


def parse_list_file(
    list_file: Path,
    base_url: str,
    default_type: str,
    default_format: str,
    default_size: str,
    default_fp: str,
    allowed_hosts: set[str] | None = None,
) -> tuple[list[str], list[str]]:
    urls: list[str] = []
    errors: list[str] = []

    lines = list_file.read_text(encoding="utf-8").splitlines()
    for idx, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("http://") or line.startswith("https://"):
            validation_error = validate_civitai_url(line, allowed_hosts=allowed_hosts)
            if validation_error:
                errors.append(f"Line {idx}: {validation_error}: {raw_line}")
                continue
            urls.append(line)
            continue

        model_part, sep, query_part = line.partition("?")
        model_str = model_part.strip()
        if not model_str.isdigit():
            errors.append(
                f"Line {idx}: expected a URL or numeric model id, got: {raw_line}"
            )
            continue

        model_id = int(model_str)
        overrides: dict[str, str] = {}
        if sep:
            for k, v in parse_qsl(query_part, keep_blank_values=True):
                if k in {"type", "format", "size", "fp"}:
                    overrides[k] = v

        built_url = build_download_url_from_values(
            base_url=base_url,
            model_id=model_id,
            type_value=overrides.get("type", default_type),
            format_value=overrides.get("format", default_format),
            size_value=overrides.get("size", default_size),
            fp_value=overrides.get("fp", default_fp),
        )
        validation_error = validate_civitai_url(built_url, allowed_hosts=allowed_hosts)
        if validation_error:
            errors.append(f"Line {idx}: {validation_error}: {raw_line}")
            continue

        urls.append(built_url)

    return urls, errors


def build_aria2c_command(
    aria2c_path: str,
    token: str,
    download_url: str,
    download_dir: Path,
    connections: int,
    out_name: str | None,
    force: bool,
) -> list[str]:
    cmd = [
        aria2c_path,
        f"--header=Authorization: Bearer {token}",
        download_url,
        f"--dir={download_dir}",
        f"--continue={'false' if force else 'true'}",
        f"--allow-overwrite={'true' if force else 'false'}",
        "--auto-file-renaming=false",
        f"--max-connection-per-server={connections}",
        f"--split={connections}",
    ]
    if out_name:
        cmd.append(f"--out={out_name}")
    return cmd


def infer_out_name_for_list_mode(download_url: str) -> str | None:
    parsed = urlparse(download_url)
    for k, v in parse_qsl(parsed.query, keep_blank_values=True):
        if k.lower() == "filename" and v:
            return v
    return None


def parse_filename_from_content_disposition(header_value: str) -> str | None:
    # Supports content-disposition variants like: attachment; filename="file.safetensors"
    if not header_value:
        return None
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', header_value, flags=re.I)
    if not match:
        return None
    filename = match.group(1).strip()
    return Path(filename).name if filename else None


def resolve_remote_file_info(download_url: str, token: str) -> tuple[str | None, int | None]:
    validation_error = validate_civitai_url(download_url)
    if validation_error:
        return None, None

    headers = {"Authorization": f"Bearer {token}"}

    try:
        req = Request(download_url, method="HEAD", headers=headers)
        with urlopen(req, timeout=30) as resp:
            disposition = resp.headers.get("Content-Disposition", "")
            filename = parse_filename_from_content_disposition(disposition)
            if not filename:
                final_url = resp.geturl()
                filename = Path(urlparse(final_url).path).name or None
            size_header = resp.headers.get("Content-Length")
            size = int(size_header) if size_header and size_header.isdigit() else None
            return filename, size
    except (HTTPError, URLError, TimeoutError):
        return None, None


def should_skip_existing(
    download_dir: Path,
    download_url: str,
    token: str,
    out_name: str | None,
) -> tuple[bool, str | None]:
    if out_name:
        target = download_dir / out_name
        return target.exists(), target.name

    filename, remote_size = resolve_remote_file_info(download_url, token)
    if not filename:
        return False, None

    target = download_dir / filename
    if not target.exists():
        return False, filename

    if remote_size is None:
        return True, filename

    return target.stat().st_size == remote_size, filename


def run_download_command(cmd: list[str], dry_run: bool) -> int:
    print("Running aria2c command:")
    print(redact_command(cmd))

    if dry_run:
        print("Dry-run mode: command not executed.")
        return 0

    try:
        completed = subprocess.run(cmd, check=False)
    except OSError as exc:
        print(f"Failed to execute aria2c: {exc}", file=sys.stderr)
        return 1

    if completed.returncode != 0:
        print(f"aria2c failed with exit code {completed.returncode}", file=sys.stderr)
        return completed.returncode

    return 0


def redact_command(cmd: list[str]) -> str:
    redacted: list[str] = []
    for item in cmd:
        if item.lower().startswith("authorization: bearer "):
            redacted.append("Authorization: Bearer ***REDACTED***")
        else:
            redacted.append(item)
    return " ".join(shlex_quote(x) for x in redacted)


def shlex_quote(s: str) -> str:
    # Lightweight shell quoting for readable logging output.
    if not s:
        return "''"
    if all(ch.isalnum() or ch in "@%_+=:,./-" for ch in s):
        return s
    return "'" + s.replace("'", "'\"'\"'") + "'"


def main() -> int:
    args = parse_args()

    aria2c_path = shutil.which("aria2c")
    if not aria2c_path:
        print("aria2c is not installed or not in PATH.", file=sys.stderr)
        return 2

    token = args.token or os.getenv("CIVITAI_API_TOKEN")
    if not token:
        print(
            "Missing token. Set CIVITAI_API_TOKEN or pass --civitai-api-token.",
            file=sys.stderr,
        )
        return 2

    download_dir = Path(args.dir).expanduser().resolve()
    download_dir.mkdir(parents=True, exist_ok=True)

    if args.list_file:
        list_path = Path(args.list_file).expanduser().resolve()
        if not list_path.exists() or not list_path.is_file():
            print(f"List file does not exist or is not a file: {list_path}", file=sys.stderr)
            return 2

        urls, parse_errors = parse_list_file(
            list_file=list_path,
            base_url=args.base_url,
            default_type=args.type,
            default_format=args.format,
            default_size=args.size,
            default_fp=args.fp,
            allowed_hosts=ALLOWED_CIVITAI_HOSTS,
        )

        if parse_errors:
            print("List file contains invalid lines:", file=sys.stderr)
            for err in parse_errors:
                print(f"  - {err}", file=sys.stderr)
            return 2

        if not urls:
            print("No valid entries found in list file.")
            return 0

        print(f"Starting batch download for {len(urls)} entries from: {list_path}")
        success = 0
        failed = 0
        skipped = 0

        for idx, url in enumerate(urls, start=1):
            print(f"\\n[{idx}/{len(urls)}] {url}")
            out_name = args.out if args.out else infer_out_name_for_list_mode(url)
            if not args.force:
                should_skip, resolved_name = should_skip_existing(
                    download_dir=download_dir,
                    download_url=url,
                    token=token,
                    out_name=out_name,
                )
                if should_skip:
                    name_display = resolved_name or out_name or "remote file"
                    print(f"Skipping existing file: {name_display}")
                    skipped += 1
                    continue

            cmd = build_aria2c_command(
                aria2c_path=aria2c_path,
                token=token,
                download_url=url,
                download_dir=download_dir,
                connections=args.connections,
                out_name=out_name,
                force=args.force,
            )
            rc = run_download_command(cmd, dry_run=args.dry_run)
            if rc == 0:
                success += 1
            else:
                failed += 1

        print(f"\\nBatch complete. Succeeded: {success}, Skipped existing: {skipped}, Failed: {failed}")
        return 0 if failed == 0 else 1

    download_url = build_download_url(args)
    validation_error = validate_civitai_url(download_url, allowed_hosts=ALLOWED_CIVITAI_HOSTS)
    if validation_error:
        print(f"Invalid download URL: {validation_error}", file=sys.stderr)
        return 2

    if not args.force:
        should_skip, resolved_name = should_skip_existing(
            download_dir=download_dir,
            download_url=download_url,
            token=token,
            out_name=args.out,
        )
        if should_skip:
            name_display = resolved_name or args.out or "remote file"
            print(f"Skipping existing file: {name_display}")
            return 0

    cmd = build_aria2c_command(
        aria2c_path=aria2c_path,
        token=token,
        download_url=download_url,
        download_dir=download_dir,
        connections=args.connections,
        out_name=args.out,
        force=args.force,
    )
    rc = run_download_command(cmd, dry_run=args.dry_run)
    if rc != 0:
        return rc

    print("Download completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
