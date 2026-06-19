#!/usr/bin/env python3
"""Upload InvokeAI images to an S3 bucket.

Examples:
  python upload_images_to_s3.py --bucket my-bucket
  python upload_images_to_s3.py --bucket my-bucket --prefix invokeai/images
  python upload_images_to_s3.py --bucket my-bucket --dry-run

Notes:
- Defaults to /workspace/invokeai/outputs/images.
- If /workspace/invokeai/ouputs/images (typo path) exists, it is accepted too.
- Uses standard AWS credential resolution (env vars, profiles, IAM role, etc.).
"""

from __future__ import annotations

import argparse
import mimetypes
import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

DEFAULT_IMAGE_DIR = Path("/workspace/invokeai/outputs/images")
TYPO_IMAGE_DIR = Path("/workspace/invokeai/ouputs/images")
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
EXCLUDED_DIR_NAMES = {"thumbnails"}


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


def resolve_source_dir(source_arg: str | None) -> Path:
    """Resolve source directory, supporting both the correct and typo InvokeAI paths."""
    if source_arg:
        return Path(source_arg).expanduser().resolve()

    if DEFAULT_IMAGE_DIR.exists():
        return DEFAULT_IMAGE_DIR
    if TYPO_IMAGE_DIR.exists():
        return TYPO_IMAGE_DIR

    return DEFAULT_IMAGE_DIR


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload images from a local directory to S3.")
    parser.add_argument(
        "--source",
        default=None,
        help=(
            "Image source directory (default: auto-detect /workspace/invokeai/outputs/images "
            "or /workspace/invokeai/ouputs/images)"
        ),
    )
    parser.add_argument("--bucket", required=True, help="Target S3 bucket name")
    parser.add_argument(
        "--prefix",
        default="",
        help="Optional key prefix in the bucket, e.g. invokeai/images",
    )
    parser.add_argument(
        "--region",
        default=None,
        help="Optional AWS region (default: boto3 resolution chain)",
    )
    parser.add_argument(
        "--include-non-images",
        action="store_true",
        help="Upload all files, not just common image extensions",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print files that would upload without making changes",
    )
    return parser.parse_args()


def build_s3_key(prefix: str, source_root: Path, file_path: Path) -> str:
    rel = file_path.relative_to(source_root).as_posix()
    clean_prefix = prefix.strip("/")
    return f"{clean_prefix}/{rel}" if clean_prefix else rel


def is_image_path(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS


def is_excluded_path(path: Path, source_root: Path) -> bool:
    """Exclude files inside known subdirectories (e.g., thumbnails)."""
    rel_parts = path.relative_to(source_root).parts
    return any(part.lower() in EXCLUDED_DIR_NAMES for part in rel_parts)


def should_skip_upload(s3_client, bucket: str, key: str, file_path: Path) -> bool:
    """Skip upload when object exists with same size (fast check)."""
    try:
        response = s3_client.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise

    return int(response.get("ContentLength", -1)) == file_path.stat().st_size


def upload_images(
    source_dir: Path,
    bucket: str,
    prefix: str,
    region: str | None,
    include_non_images: bool,
    dry_run: bool,
) -> tuple[int, int, int]:
    session = boto3.session.Session(region_name=region)
    s3_client = session.client("s3")

    all_files = sorted(
        p for p in source_dir.rglob("*") if p.is_file() and not is_excluded_path(p, source_dir)
    )
    candidates = all_files if include_non_images else [p for p in all_files if is_image_path(p)]

    if not candidates:
        print(f"No matching files found in {source_dir}")
        return 0, 0, 0

    uploaded = 0
    skipped = 0
    failed = 0

    print(f"Found {len(candidates)} file(s) to evaluate in {source_dir}")

    for i, file_path in enumerate(candidates, start=1):
        key = build_s3_key(prefix, source_dir, file_path)

        try:
            if should_skip_upload(s3_client, bucket, key, file_path):
                skipped += 1
                print(f"[{i}/{len(candidates)}] skip   s3://{bucket}/{key}")
                continue

            if dry_run:
                print(f"[{i}/{len(candidates)}] upload s3://{bucket}/{key}")
                continue

            content_type, _ = mimetypes.guess_type(file_path.name)
            extra_args = {"ContentType": content_type} if content_type else None

            if extra_args:
                s3_client.upload_file(str(file_path), bucket, key, ExtraArgs=extra_args)
            else:
                s3_client.upload_file(str(file_path), bucket, key)

            uploaded += 1
            print(f"[{i}/{len(candidates)}] done   s3://{bucket}/{key}")
        except (ClientError, BotoCoreError) as exc:
            failed += 1
            print(f"[{i}/{len(candidates)}] fail   {file_path}: {exc}", file=sys.stderr)

    return uploaded, skipped, failed


def main() -> int:
    args = parse_args()
    normalize_custom_aws_env_vars()

    source_dir = resolve_source_dir(args.source)
    if not source_dir.exists() or not source_dir.is_dir():
        print(f"Source directory does not exist or is not a directory: {source_dir}", file=sys.stderr)
        return 2

    try:
        uploaded, skipped, failed = upload_images(
            source_dir=source_dir,
            bucket=args.bucket,
            prefix=args.prefix,
            region=args.region,
            include_non_images=args.include_non_images,
            dry_run=args.dry_run,
        )
    except NoCredentialsError:
        print("AWS credentials not found. Set env vars, profile, or use IAM role.", file=sys.stderr)
        return 3
    except Exception as exc:
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print("Dry run complete.")
        return 0

    print(f"Uploaded: {uploaded}, Skipped: {skipped}, Failed: {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
