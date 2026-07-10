from __future__ import annotations

import fnmatch
import logging
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from .manifest_store import load_manifest, save_manifest, set_manifest_entry

logger = logging.getLogger(__name__)

SMALL_UPLOAD_LIMIT = 4 * 1024 * 1024
UPLOAD_CHUNK_SIZE = 5 * 1024 * 1024


def _normalize_globs(globs_csv: str) -> list[str]:
    return [pattern.strip() for pattern in globs_csv.split(",") if pattern.strip()]


def _is_included(rel_path: str, includes: list[str], excludes: list[str]) -> bool:
    if includes and not any(fnmatch.fnmatch(rel_path, pattern) for pattern in includes):
        return False
    if excludes and any(fnmatch.fnmatch(rel_path, pattern) for pattern in excludes):
        return False
    return True


def _resolve_local_root(base_root: str, local_subpath: str) -> Path:
    root = Path(base_root).resolve()
    candidate = (root / local_subpath).resolve() if local_subpath else root
    if root not in [candidate, *candidate.parents]:
        raise ValueError("Selected path escapes allowed sync root")
    if not candidate.exists() or not candidate.is_dir():
        raise ValueError("Selected local path does not exist or is not a directory")
    return candidate


def _graph_path(path: str) -> str:
    return quote(path.strip("/"), safe="/")


def _approot_item_url(remote_path: str) -> str:
    clean = remote_path.strip("/")
    if not clean:
        return "https://graph.microsoft.com/v1.0/me/drive/special/approot"
    return f"https://graph.microsoft.com/v1.0/me/drive/special/approot:/{_graph_path(clean)}"


def _local_signature(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


async def _get_remote_file_metadata(client: httpx.AsyncClient, headers: dict[str, str], remote_file_path: str) -> dict[str, Any] | None:
    url = _approot_item_url(remote_file_path)
    response = await client.get(url, headers=headers)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


async def ensure_remote_folder(client: httpx.AsyncClient, headers: dict[str, str], remote_folder: str) -> None:
    # Trigger app folder materialization if it doesn't exist yet.
    await client.get("https://graph.microsoft.com/v1.0/me/drive/special/approot", headers=headers)

    folder = remote_folder.strip("/")
    if not folder:
        return

    current = ""
    for segment in folder.split("/"):
        current = f"{current}/{segment}" if current else segment
        check_url = _approot_item_url(current)
        check_response = await client.get(check_url, headers=headers)
        if check_response.status_code == 404:
            parent = current.rsplit("/", 1)[0] if "/" in current else ""
            children_url = (
                "https://graph.microsoft.com/v1.0/me/drive/special/approot/children"
                if not parent
                else f"https://graph.microsoft.com/v1.0/me/drive/special/approot:/{_graph_path(parent)}:/children"
            )
            create_response = await client.post(
                children_url,
                headers=headers,
                json={
                    "name": segment,
                    "folder": {},
                    "@microsoft.graph.conflictBehavior": "replace",
                },
            )
            create_response.raise_for_status()
            logger.debug("Created remote folder segment: %s", current)
        else:
            check_response.raise_for_status()


async def build_sync_plan(
    access_token: str,
    base_root: str,
    local_subpath: str,
    remote_folder: str,
    include_globs: str,
    exclude_globs: str,
    conflict_behavior: str,
    force_rescan: bool = False,
) -> dict[str, Any]:
    local_root = _resolve_local_root(base_root, local_subpath)
    includes = _normalize_globs(include_globs)
    excludes = _normalize_globs(exclude_globs)

    headers = {"Authorization": f"Bearer {access_token}"}
    plan_items: list[dict[str, Any]] = []
    skipped = 0
    manifest_hits = 0
    manifest = load_manifest()

    async with httpx.AsyncClient(timeout=30) as client:
        for path in sorted(local_root.rglob("*")):
            if not path.is_file():
                continue
            rel_path = path.relative_to(local_root).as_posix()
            if not _is_included(rel_path, includes, excludes):
                skipped += 1
                continue

            remote_path = f"{remote_folder.strip('/')}/{rel_path}" if remote_folder.strip("/") else rel_path
            stat = path.stat()
            signature = _local_signature(path)
            manifest_entry = manifest.get(remote_path)

            if (not force_rescan) and manifest_entry and manifest_entry.get("local_signature") == signature:
                action = "skip"
                reason = "manifest_unchanged"
                manifest_hits += 1
                remote_meta = None
            else:
                remote_meta = await _get_remote_file_metadata(client, headers, remote_path)

                action = "upload"
                reason = "new"
                if remote_meta is not None:
                    if remote_meta.get("size") == stat.st_size:
                        action = "skip"
                        reason = "unchanged_remote"
                    else:
                        action = "upload"
                        reason = "size_changed"

            plan_items.append(
                {
                    "local_path": str(path),
                    "relative_path": rel_path,
                    "remote_path": remote_path,
                    "size": stat.st_size,
                    "local_signature": signature,
                    "action": action,
                    "reason": reason,
                }
            )

    uploads = [item for item in plan_items if item["action"] == "upload"]
    unchanged = [item for item in plan_items if item["action"] == "skip"]
    bytes_total = sum(item["size"] for item in uploads)

    logger.info(
        "Sync plan built for %s -> %s: %d to upload, %d unchanged, %d excluded",
        local_root,
        remote_folder.strip("/"),
        len(uploads),
        len(unchanged),
        skipped,
    )

    return {
        "local_root": str(local_root),
        "remote_folder": remote_folder.strip("/"),
        "summary": {
            "scanned_files": len(plan_items),
            "to_upload": len(uploads),
            "unchanged": len(unchanged),
            "excluded": skipped,
            "manifest_hits": manifest_hits,
            "force_rescan": force_rescan,
            "bytes_to_upload": bytes_total,
        },
        "items": plan_items,
        "conflict_behavior": conflict_behavior,
    }


async def _upload_small_file(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    local_path: str,
    remote_path: str,
) -> None:
    url = f"https://graph.microsoft.com/v1.0/me/drive/special/approot:/{_graph_path(remote_path)}:/content"
    data = Path(local_path).read_bytes()
    response = await client.put(url, headers=headers, content=data)
    response.raise_for_status()


async def _upload_large_file(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    local_path: str,
    remote_path: str,
    conflict_behavior: str,
) -> None:
    create_url = f"https://graph.microsoft.com/v1.0/me/drive/special/approot:/{_graph_path(remote_path)}:/createUploadSession"
    session_response = await client.post(
        create_url,
        headers=headers,
        json={"item": {"@microsoft.graph.conflictBehavior": conflict_behavior}},
    )
    session_response.raise_for_status()
    upload_url = session_response.json()["uploadUrl"]

    path = Path(local_path)
    total_size = path.stat().st_size
    start = 0

    with path.open("rb") as file_obj:
        while start < total_size:
            chunk = file_obj.read(UPLOAD_CHUNK_SIZE)
            end = start + len(chunk) - 1
            chunk_headers = {
                "Content-Length": str(len(chunk)),
                "Content-Range": f"bytes {start}-{end}/{total_size}",
            }
            upload_response = await client.put(upload_url, headers=chunk_headers, content=chunk)
            if upload_response.status_code not in (200, 201, 202):
                upload_response.raise_for_status()
            start = end + 1


async def execute_sync_plan(
    access_token: str,
    plan: dict[str, Any],
    progress_callback,
    event_callback,
    max_retries: int = 3,
) -> None:
    headers = {"Authorization": f"Bearer {access_token}"}
    remote_folder = plan.get("remote_folder", "")
    conflict_behavior = plan.get("conflict_behavior", "replace")
    manifest = load_manifest()

    async with httpx.AsyncClient(timeout=60) as client:
        await ensure_remote_folder(client, headers, remote_folder)

        upload_items = [item for item in plan.get("items", []) if item.get("action") == "upload"]
        skip_items = [item for item in plan.get("items", []) if item.get("action") == "skip"]
        bytes_uploaded = 0
        files_uploaded = 0
        files_failed = 0

        for item in skip_items:
            set_manifest_entry(
                manifest,
                remote_path=item["remote_path"],
                local_signature=item["local_signature"],
                size=item["size"],
            )

        for item in upload_items:
            size = item["size"]
            await event_callback(f"Uploading {item['relative_path']} ({size} bytes)")
            attempt = 0
            while True:
                try:
                    if size <= SMALL_UPLOAD_LIMIT:
                        await _upload_small_file(client, headers, item["local_path"], item["remote_path"])
                    else:
                        await _upload_large_file(
                            client,
                            headers,
                            item["local_path"],
                            item["remote_path"],
                            conflict_behavior=conflict_behavior,
                        )
                    break
                except Exception as exc:
                    attempt += 1
                    if attempt > max_retries:
                        files_failed += 1
                        logger.warning(
                            "Upload failed for %s after %d retries", item["relative_path"], max_retries, exc_info=True
                        )
                        await progress_callback(
                            files_uploaded=files_uploaded,
                            bytes_uploaded=bytes_uploaded,
                            files_failed=files_failed,
                        )
                        await event_callback(
                            f"Failed {item['relative_path']} after {max_retries} retries: {exc}"
                        )
                        raise
                    logger.info(
                        "Retry %d/%d for %s due to: %s", attempt, max_retries, item["relative_path"], exc
                    )
                    await event_callback(
                        f"Retry {attempt}/{max_retries} for {item['relative_path']} due to: {exc}"
                    )

            set_manifest_entry(
                manifest,
                remote_path=item["remote_path"],
                local_signature=item["local_signature"],
                size=size,
            )

            bytes_uploaded += size
            files_uploaded += 1
            await progress_callback(
                files_uploaded=files_uploaded,
                bytes_uploaded=bytes_uploaded,
                files_failed=files_failed,
            )
            await event_callback(f"Uploaded {item['relative_path']}")

    save_manifest(manifest)
