#!/usr/bin/env python3
"""Upload a local directory tree to an AWS S3 bucket.

Example:
  python upload_models_to_s3.py \
    --bucket my-bucket \
    --prefix invokeai/models \
    --source /workspace/invokeai/models

Authentication:
  Uses standard AWS credential resolution (env vars, shared config, IAM role, etc.).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import threading
import time
from pathlib import Path

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
        description="Recursively upload a local directory to an S3 bucket."
    )
    parser.add_argument(
        "--source",
        default="/workspace/invokeai/models",
        help="Local source directory to upload (default: /workspace/invokeai/models)",
    )
    parser.add_argument("--bucket", required=True, help="Target S3 bucket name")
    parser.add_argument(
        "--prefix",
        default="",
        help="Optional S3 key prefix (e.g. backups/invokeai-models)",
    )
    parser.add_argument(
        "--region",
        default=None,
        help="Optional AWS region (if omitted, boto3 default resolution is used)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be uploaded without uploading",
    )
    return parser.parse_args()


def build_s3_key(prefix: str, source_root: Path, file_path: Path) -> str:
    relative = file_path.relative_to(source_root).as_posix()
    cleaned_prefix = prefix.strip("/")
    if cleaned_prefix:
        return f"{cleaned_prefix}/{relative}"
    return relative


def md5_hex(file_path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    """Compute the local file MD5 for ETag comparison on simple uploads."""
    digest = hashlib.md5()
    with file_path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def human_size(num_bytes: int) -> str:
    """Format bytes as a human-readable string."""
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
    """Return a textual progress bar string."""
    if total <= 0:
        return "[" + ("-" * width) + "]"
    ratio = max(0.0, min(1.0, current / total))
    filled = int(ratio * width)
    return "[" + ("#" * filled) + ("-" * (width - filled)) + "]"


class UploadProgress:
    """Print a live progress bar for a single file upload."""

    def __init__(self, file_path: Path, file_index: int, total_files: int, total_bytes: int):
        self.file_path = file_path
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
            # Throttle redraws to keep output readable and reduce terminal overhead.
            if now - self._last_print_at < 0.1 and self.transferred < self.total_bytes:
                return
            self._last_print_at = now

            bar = render_progress_bar(self.transferred, self.total_bytes)
            pct = (self.transferred / self.total_bytes) * 100
            line = (
                f"\r[{self.file_index}/{self.total_files}] {self.file_path.name} "
                f"{bar} {pct:6.2f}% "
                f"{human_size(self.transferred)}/{human_size(self.total_bytes)}"
            )
            print(line, end="", flush=True)

            if self.transferred >= self.total_bytes:
                print()


def should_skip_upload(s3_client, bucket: str, key: str, file_path: Path) -> bool:
    """Return True when local file appears unchanged compared to S3 metadata."""
    try:
        obj = s3_client.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise

    local_size = file_path.stat().st_size
    remote_size = obj.get("ContentLength")
    if remote_size != local_size:
        return False

    # For files uploaded as single-part objects, ETag is usually the MD5 digest.
    etag = str(obj.get("ETag", "")).strip('"')
    if etag and "-" not in etag:
        return etag == md5_hex(file_path)

    # For multipart/KMS cases where ETag is not a plain MD5, use size match fallback.
    return True


def classify_files_for_upload(s3_client, bucket: str, source_dir: Path, prefix: str):
    """Split local files into upload-needed vs unchanged and gather scan stats."""
    files = sorted(p for p in source_dir.rglob("*") if p.is_file())
    to_upload: list[tuple[Path, str, int]] = []
    skipped = 0
    failed = 0
    total_local_bytes = sum(p.stat().st_size for p in files)

    for index, file_path in enumerate(files, start=1):
        key = build_s3_key(prefix, source_dir, file_path)
        try:
            if should_skip_upload(s3_client, bucket, key, file_path):
                skipped += 1
            else:
                to_upload.append((file_path, key, file_path.stat().st_size))
        except (ClientError, BotoCoreError) as exc:
            failed += 1
            print(
                f"Scan failed for {file_path} (#{index}/{len(files)}): {exc}",
                file=sys.stderr,
            )

    return files, to_upload, skipped, failed, total_local_bytes


def upload_directory(
    source_dir: Path, bucket: str, prefix: str, region: str | None, dry_run: bool
) -> tuple[int, int, int]:
    session = boto3.session.Session(region_name=region)
    s3_client = session.client("s3")

    uploaded = 0
    skipped = 0
    failed = 0

    print("Scanning local files and comparing with S3...")
    files, to_upload, skipped, scan_failed, total_local_bytes = classify_files_for_upload(
        s3_client=s3_client,
        bucket=bucket,
        source_dir=source_dir,
        prefix=prefix,
    )
    failed += scan_failed

    if not files:
        print(f"No files found under: {source_dir}")
        return uploaded, skipped, failed

    upload_bytes_total = sum(file_size for _, _, file_size in to_upload)
    print(f"Total local files: {len(files)} ({human_size(total_local_bytes)})")
    print(f"Files to upload: {len(to_upload)} ({human_size(upload_bytes_total)})")
    print(f"Unchanged files to skip: {skipped}")
    if failed:
        print(f"Scan failures: {failed}")

    if dry_run:
        print("Dry-run mode: no files uploaded.")
        return uploaded, skipped, failed

    if not to_upload:
        print("Everything is already up to date. Nothing to upload.")
        return uploaded, skipped, failed

    print("Starting uploads...")

    for index, (file_path, key, file_size) in enumerate(to_upload, start=1):
        print(f"Uploading [{index}/{len(to_upload)}]: {file_path} -> s3://{bucket}/{key}")
        progress = UploadProgress(
            file_path=file_path,
            file_index=index,
            total_files=len(to_upload),
            total_bytes=file_size,
        )

        try:
            s3_client.upload_file(str(file_path), bucket, key, Callback=progress)
            uploaded += 1
            print(f"Completed: {file_path.name}")
        except (ClientError, BotoCoreError) as exc:
            failed += 1
            print(f"Upload failed for {file_path}: {exc}", file=sys.stderr)

    return uploaded, skipped, failed


def main() -> int:
    args = parse_args()
    normalize_custom_aws_env_vars()

    source_dir = Path(args.source).expanduser().resolve()
    if not source_dir.exists() or not source_dir.is_dir():
        print(f"Source directory does not exist or is not a directory: {source_dir}")
        return 2

    try:
        uploaded, skipped, failed = upload_directory(
            source_dir=source_dir,
            bucket=args.bucket,
            prefix=args.prefix,
            region=args.region,
            dry_run=args.dry_run,
        )
    except NoCredentialsError:
        print(
            "AWS credentials not found. Configure credentials via env vars, "
            "AWS profile, or IAM role.",
            file=sys.stderr,
        )
        return 3
    except Exception as exc:  # Defensive fallback for unexpected runtime issues
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print("Dry run completed.")
        return 0

    print(f"Uploaded: {uploaded}, Skipped unchanged: {skipped}, Failed: {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
