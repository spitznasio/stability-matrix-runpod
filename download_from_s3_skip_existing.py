#!/usr/bin/env python3
"""Download files from an S3 bucket while skipping existing local files.

Example:
  python download_from_s3_skip_existing.py \
    --bucket my-bucket \
    --prefix runpod/invokeai/models \
    --dest /workspace/invokeai/models
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path, PurePosixPath

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError


def normalize_custom_aws_env_vars() -> None:
    """Map custom AWS_S3_* variables to standard AWS SDK variable names."""
    access_key = os.getenv("AWS_S3_ACCESS_KEY")
    secret_key = os.getenv("AWS_S3_SECRET_KEY")
    session_token = os.getenv("AWS_S3_SESSION_TOKEN")

    if access_key and not os.getenv("AWS_ACCESS_KEY_ID"):
        os.environ["AWS_ACCESS_KEY_ID"] = access_key

    if secret_key and not os.getenv("AWS_SECRET_ACCESS_KEY"):
        os.environ["AWS_SECRET_ACCESS_KEY"] = secret_key

    if session_token and not os.getenv("AWS_SESSION_TOKEN"):
        os.environ["AWS_SESSION_TOKEN"] = session_token


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download files from S3 and skip files that already exist locally."
    )
    parser.add_argument("--bucket", required=True, help="Source S3 bucket name")
    parser.add_argument(
        "--prefix",
        default="",
        help="Optional source key prefix to download from (e.g. runpod/invokeai/models)",
    )
    parser.add_argument(
        "--dest",
        default="/workspace/invokeai/models",
        help="Local destination directory (default: /workspace/invokeai/models)",
    )
    parser.add_argument(
        "--region",
        default=None,
        help="Optional AWS region (if omitted, boto3 default resolution is used)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be downloaded without downloading",
    )
    return parser.parse_args()


def human_size(num_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(num_bytes)
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{num_bytes} B"


def render_progress_bar(current: int, total: int, width: int = 28) -> str:
    if total <= 0:
        return "[" + ("-" * width) + "]"
    ratio = max(0.0, min(1.0, current / total))
    filled = int(ratio * width)
    return "[" + ("#" * filled) + ("-" * (width - filled)) + "]"


class DownloadProgress:
    """Print a live progress bar for a single file download."""

    def __init__(self, file_name: str, file_index: int, total_files: int, total_bytes: int):
        self.file_name = file_name
        self.file_index = file_index
        self.total_files = total_files
        self.total_bytes = max(1, total_bytes)
        self.transferred = 0
        self._lock = threading.Lock()
        self._last_print_at = 0.0

    def __call__(self, bytes_amount: int) -> None:
        with self._lock:
            self.transferred += bytes_amount
            now = time.time()
            if now - self._last_print_at < 0.1 and self.transferred < self.total_bytes:
                return
            self._last_print_at = now

            bar = render_progress_bar(self.transferred, self.total_bytes)
            pct = (self.transferred / self.total_bytes) * 100
            line = (
                f"\r[{self.file_index}/{self.total_files}] {self.file_name} "
                f"{bar} {pct:6.2f}% "
                f"{human_size(self.transferred)}/{human_size(self.total_bytes)}"
            )
            print(line, end="", flush=True)

            if self.transferred >= self.total_bytes:
                print()


def iter_s3_objects(s3_client, bucket: str, prefix: str):
    paginator = s3_client.get_paginator("list_objects_v2")
    kwargs = {"Bucket": bucket}
    cleaned_prefix = prefix.strip("/")
    if cleaned_prefix:
        kwargs["Prefix"] = cleaned_prefix + "/"

    for page in paginator.paginate(**kwargs):
        for obj in page.get("Contents", []):
            key = obj.get("Key")
            if not key or key.endswith("/"):
                continue
            yield key, int(obj.get("Size", 0))


def safe_local_path(dest_root: Path, key: str, prefix: str) -> Path:
    cleaned_prefix = prefix.strip("/")
    if cleaned_prefix:
        prefix_with_slash = cleaned_prefix + "/"
        relative_key = key[len(prefix_with_slash) :] if key.startswith(prefix_with_slash) else key
    else:
        relative_key = key

    rel_path = PurePosixPath(relative_key)
    target_path = (dest_root / Path(*rel_path.parts)).resolve()

    # Guard against path traversal from unexpected key names.
    try:
        target_path.relative_to(dest_root)
    except ValueError as exc:
        raise ValueError(f"Unsafe key path detected: {key}") from exc

    return target_path


def main() -> int:
    args = parse_args()
    normalize_custom_aws_env_vars()

    dest_root = Path(args.dest).expanduser().resolve()
    dest_root.mkdir(parents=True, exist_ok=True)

    session = boto3.session.Session(region_name=args.region)
    s3_client = session.client("s3")

    try:
        print("Scanning bucket objects...")
        objects = list(iter_s3_objects(s3_client, args.bucket, args.prefix))
    except NoCredentialsError:
        print(
            "AWS credentials not found. Configure credentials via env vars, "
            "AWS profile, or IAM role.",
            file=sys.stderr,
        )
        return 3
    except (ClientError, BotoCoreError) as exc:
        print(f"Failed to list bucket objects: {exc}", file=sys.stderr)
        return 1

    if not objects:
        print("No files found in the selected bucket/prefix.")
        return 0

    to_download: list[tuple[str, int, Path]] = []
    skipped_existing = 0
    total_remote_bytes = 0

    for key, size in objects:
        total_remote_bytes += size
        try:
            local_path = safe_local_path(dest_root, key, args.prefix)
        except ValueError as exc:
            print(f"Skipping unsafe key {key}: {exc}", file=sys.stderr)
            continue

        if local_path.exists():
            skipped_existing += 1
            continue

        to_download.append((key, size, local_path))

    download_bytes = sum(size for _, size, _ in to_download)
    print(f"Total remote files: {len(objects)} ({human_size(total_remote_bytes)})")
    print(f"Files to download: {len(to_download)} ({human_size(download_bytes)})")
    print(f"Skipped existing local files: {skipped_existing}")

    if args.dry_run:
        print("Dry-run mode: no files downloaded.")
        return 0

    if not to_download:
        print("All files already exist locally. Nothing to download.")
        return 0

    downloaded = 0
    failed = 0

    print("Starting downloads...")
    for index, (key, size, local_path) in enumerate(to_download, start=1):
        local_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading [{index}/{len(to_download)}]: s3://{args.bucket}/{key} -> {local_path}")

        progress = DownloadProgress(
            file_name=local_path.name,
            file_index=index,
            total_files=len(to_download),
            total_bytes=size,
        )

        try:
            s3_client.download_file(args.bucket, key, str(local_path), Callback=progress)
            downloaded += 1
            print(f"Completed: {local_path.name}")
        except (ClientError, BotoCoreError) as exc:
            failed += 1
            print(f"Download failed for {key}: {exc}", file=sys.stderr)

    print(
        f"Downloaded: {downloaded}, Skipped existing: {skipped_existing}, "
        f"Failed: {failed}"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
