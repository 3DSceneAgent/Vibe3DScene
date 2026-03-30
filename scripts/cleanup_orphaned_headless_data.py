#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from redis import Redis

from scene_agent.config import get_settings


@dataclass(frozen=True)
class CleanupReport:
    scene_root: str
    image_root: str
    redis_url: str
    redis_key_prefix: str
    checkpoint_threads: list[str]
    runtime_threads: list[str]
    image_metadata_threads: list[str]
    disk_orphan_scene_dirs: list[str]
    disk_orphan_image_dirs: list[str]
    checkpoint_orphans: list[str]
    image_metadata_orphans: list[str]
    runtime_metadata_orphans: list[str]


def _scan_checkpoint_threads(client: Redis, prefix: str) -> list[str]:
    seen: set[str] = set()
    prefix_text = f"{prefix}:ckpt:"
    suffix_text = ":index"
    pattern = f"{prefix}:ckpt:*:*:index"
    for raw_key in client.scan_iter(pattern):
        key = str(raw_key)
        if not key.startswith(prefix_text) or not key.endswith(suffix_text):
            continue
        body = key[len(prefix_text) : -len(suffix_text)]
        if ":" not in body:
            continue
        thread_id = body.rsplit(":", 1)[0]
        if thread_id:
            seen.add(thread_id)
    return sorted(seen)


def _scan_runtime_threads(client: Redis, prefix: str) -> list[str]:
    seen: set[str] = set()
    meta_prefix = f"{prefix}:{{session:"
    meta_suffix = "}:meta"
    lease_suffix = "}:lease"
    fence_suffix = "}:fence"
    vlm_prefix = f"{prefix}:thread_vlm:"

    for pattern, suffix in (
        (f"{prefix}:{{session:*}}:meta", meta_suffix),
        (f"{prefix}:{{session:*}}:lease", lease_suffix),
        (f"{prefix}:{{session:*}}:fence", fence_suffix),
    ):
        for raw_key in client.scan_iter(pattern):
            key = str(raw_key)
            if not key.startswith(meta_prefix) or not key.endswith(suffix):
                continue
            thread_id = key[len(meta_prefix) : -len(suffix)]
            if thread_id:
                seen.add(thread_id)

    for raw_key in client.scan_iter(f"{prefix}:thread_vlm:*"):
        key = str(raw_key)
        if not key.startswith(vlm_prefix):
            continue
        thread_id = key[len(vlm_prefix) :]
        if thread_id:
            seen.add(thread_id)

    sessions_last_active_key = f"{prefix}:sessions:last_active"
    for thread_id in client.zrevrange(sessions_last_active_key, 0, -1):
        if isinstance(thread_id, str) and thread_id:
            seen.add(thread_id)
    return sorted(seen)


def _scan_image_metadata_threads(client: Redis, prefix: str) -> list[str]:
    seen: set[str] = set()
    image_prefix = f"{prefix}:img:"
    image_suffix = ":order"
    for raw_key in client.scan_iter(f"{prefix}:img:*:order"):
        key = str(raw_key)
        if not key.startswith(image_prefix) or not key.endswith(image_suffix):
            continue
        thread_id = key[len(image_prefix) : -len(image_suffix)]
        if thread_id:
            seen.add(thread_id)
    return sorted(seen)


def _build_report(client: Redis) -> CleanupReport:
    settings = get_settings()
    prefix = settings.redis_key_prefix
    scene_root = Path(settings.session_shared_storage_root)
    image_root = Path(settings.reference_image_storage_dir)

    scene_dirs = sorted(path.name for path in scene_root.iterdir() if path.is_dir()) if scene_root.exists() else []
    image_dirs = sorted(path.name for path in image_root.iterdir() if path.is_dir()) if image_root.exists() else []
    checkpoint_threads = _scan_checkpoint_threads(client, prefix)
    runtime_threads = _scan_runtime_threads(client, prefix)
    image_metadata_threads = _scan_image_metadata_threads(client, prefix)

    scene_set = set(scene_dirs)
    image_set = set(image_dirs)
    checkpoint_set = set(checkpoint_threads)
    runtime_set = set(runtime_threads)
    image_metadata_set = set(image_metadata_threads)
    known_thread_ids = checkpoint_set | runtime_set | image_metadata_set

    return CleanupReport(
        scene_root=str(scene_root),
        image_root=str(image_root),
        redis_url=settings.redis_url,
        redis_key_prefix=prefix,
        checkpoint_threads=checkpoint_threads,
        runtime_threads=runtime_threads,
        image_metadata_threads=image_metadata_threads,
        disk_orphan_scene_dirs=sorted(scene_set - known_thread_ids),
        disk_orphan_image_dirs=sorted(image_set - known_thread_ids),
        checkpoint_orphans=sorted(checkpoint_set - scene_set - image_set - runtime_set - image_metadata_set),
        image_metadata_orphans=sorted(image_metadata_set - scene_set - image_set - checkpoint_set - runtime_set),
        runtime_metadata_orphans=sorted(runtime_set - scene_set - image_set - checkpoint_set - image_metadata_set),
    )


def _delete_matching_keys(client: Redis, patterns: list[str]) -> int:
    keys: set[str] = set()
    for pattern in patterns:
        for raw_key in client.scan_iter(pattern):
            key = str(raw_key)
            if key:
                keys.add(key)
    if not keys:
        return 0
    return int(client.delete(*sorted(keys)))


def _cleanup_checkpoint_orphan(client: Redis, prefix: str, thread_id: str) -> int:
    return _delete_matching_keys(
        client,
        [
            f"{prefix}:ckpt:{thread_id}:*",
            f"{prefix}:ckpt_blob:{thread_id}:*",
            f"{prefix}:writes:{thread_id}:*",
        ],
    )


def _cleanup_image_metadata_orphan(client: Redis, prefix: str, thread_id: str) -> int:
    return _delete_matching_keys(
        client,
        [
            f"{prefix}:img:{thread_id}:*",
            f"{prefix}:imgbind:{thread_id}:*",
        ],
    )


def _cleanup_runtime_metadata_orphan(client: Redis, prefix: str, thread_id: str) -> int:
    deleted = _delete_matching_keys(
        client,
        [
            f"{prefix}:{{session:{thread_id}}}:meta",
            f"{prefix}:{{session:{thread_id}}}:lease",
            f"{prefix}:{{session:{thread_id}}}:fence",
            f"{prefix}:thread_vlm:{thread_id}",
        ],
    )
    client.zrem(f"{prefix}:sessions:last_active", thread_id)
    for raw_key in client.scan_iter(f"{prefix}:worker:*:sessions"):
        client.srem(str(raw_key), thread_id)
    return deleted


def _probe_runtime_in_use(api_port: int) -> int | None:
    url = f"http://127.0.0.1:{api_port}/headless/session-capacity"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return None
    in_use = payload.get("in_use")
    return int(in_use) if isinstance(in_use, int) else None


def _print_report(report: CleanupReport) -> None:
    payload = {
        "scene_root": report.scene_root,
        "image_root": report.image_root,
        "redis_url": report.redis_url,
        "redis_key_prefix": report.redis_key_prefix,
        "checkpoint_threads": report.checkpoint_threads,
        "runtime_threads": report.runtime_threads,
        "image_metadata_threads": report.image_metadata_threads,
        "disk_orphan_scene_dirs": report.disk_orphan_scene_dirs,
        "disk_orphan_image_dirs": report.disk_orphan_image_dirs,
        "checkpoint_orphans": report.checkpoint_orphans,
        "image_metadata_orphans": report.image_metadata_orphans,
        "runtime_metadata_orphans": report.runtime_metadata_orphans,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect and optionally clean orphaned headless scene directories and Redis thread metadata."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete the discovered orphaned directories and Redis metadata.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow cleanup even if the local API reports occupied headless runtime slots.",
    )
    args = parser.parse_args()

    settings = get_settings()
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        client.ping()
    except Exception as exc:
        print(f"Redis connection failed: {settings.redis_url}: {exc}", file=sys.stderr)
        return 1

    if not args.force:
        in_use = _probe_runtime_in_use(settings.api_port)
        if in_use and in_use > 0:
            print(
                f"Refusing cleanup because the local API reports {in_use} occupied headless runtime slot(s). "
                "Re-run with --force if you are certain cleanup is safe.",
                file=sys.stderr,
            )
            return 2

    report = _build_report(client)
    _print_report(report)

    if not args.apply:
        return 0

    deleted_scene_dirs: list[str] = []
    deleted_image_dirs: list[str] = []
    deleted_checkpoint_orphans: dict[str, int] = {}
    deleted_image_metadata_orphans: dict[str, int] = {}
    deleted_runtime_metadata_orphans: dict[str, int] = {}

    for thread_id in report.disk_orphan_scene_dirs:
        path = Path(report.scene_root) / thread_id
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
            deleted_scene_dirs.append(thread_id)

    for thread_id in report.disk_orphan_image_dirs:
        path = Path(report.image_root) / thread_id
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
            deleted_image_dirs.append(thread_id)

    for thread_id in report.checkpoint_orphans:
        deleted_checkpoint_orphans[thread_id] = _cleanup_checkpoint_orphan(client, report.redis_key_prefix, thread_id)

    for thread_id in report.image_metadata_orphans:
        deleted_image_metadata_orphans[thread_id] = _cleanup_image_metadata_orphan(
            client,
            report.redis_key_prefix,
            thread_id,
        )

    for thread_id in report.runtime_metadata_orphans:
        deleted_runtime_metadata_orphans[thread_id] = _cleanup_runtime_metadata_orphan(
            client,
            report.redis_key_prefix,
            thread_id,
        )

    result = {
        "deleted_scene_dirs": deleted_scene_dirs,
        "deleted_image_dirs": deleted_image_dirs,
        "deleted_checkpoint_orphans": deleted_checkpoint_orphans,
        "deleted_image_metadata_orphans": deleted_image_metadata_orphans,
        "deleted_runtime_metadata_orphans": deleted_runtime_metadata_orphans,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
