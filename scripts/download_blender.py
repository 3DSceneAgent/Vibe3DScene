#!/usr/bin/env python3
"""
Download Blender 4.2 artifacts with platform-based selection and local cache.

Supported target platforms:
- linux-x86-64
- macos-x64
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Final

BASE_URL: Final[str] = "https://download.blender.org/release/Blender4.2"
DEFAULT_VERSION: Final[str] = "4.2.15"
FILE_TEMPLATES: Final[dict[str, str]] = {
    "linux-x86-64": "blender-{version}-linux-x64.tar.xz",
    "macos-x64": "blender-{version}-macos-x64.dmg",
}
DEFAULT_CACHE_DIR: Final[Path] = Path.home() / ".cache" / "3dsceneagent" / "blender"
DEFAULT_HTTP_HEADERS: Final[dict[str, str]] = {
    "User-Agent": "3DSceneAgent-BlenderDownloader/1.0",
    "Accept": "*/*",
}


def detect_platform() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "linux" and machine in {"x86_64", "amd64"}:
        return "linux-x86-64"
    if system == "darwin" and machine == "x86_64":
        return "macos-x64"

    raise ValueError(
        f"Unsupported host platform: system={platform.system()} machine={platform.machine()}. "
        "Only linux-x86-64 and macos-x64 are supported."
    )


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def fetch_sha256(version: str, filename: str) -> str:
    checksum_url = f"{BASE_URL}/blender-{version}.sha256"
    request = urllib.request.Request(checksum_url, headers=DEFAULT_HTTP_HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8", errors="replace")

    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        digest = parts[0].strip()
        listed = parts[-1].strip()
        if listed == filename or listed.endswith(f"/{filename}"):
            return digest.lower()

    raise RuntimeError(f"Failed to find checksum entry for {filename} in blender-{version}.sha256.")


def download_to_path(url: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        delete=False,
        dir=str(output_path.parent),
        prefix=f".{output_path.name}.",
        suffix=".tmp",
    ) as tmp_file:
        tmp_path = Path(tmp_file.name)
    try:
        request = urllib.request.Request(url, headers=DEFAULT_HTTP_HEADERS)
        with urllib.request.urlopen(request, timeout=60) as response, tmp_path.open("wb") as target:
            shutil.copyfileobj(response, target, length=1024 * 1024)
        tmp_path.replace(output_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def ensure_cached_package(
    *,
    version: str,
    target_platform: str,
    cache_dir: Path,
    force: bool,
    verify_checksum: bool,
) -> tuple[Path, str]:
    if target_platform not in FILE_TEMPLATES:
        raise ValueError(f"Unsupported target platform: {target_platform}")

    filename = FILE_TEMPLATES[target_platform].format(version=version)
    package_url = f"{BASE_URL}/{filename}"
    cache_path = cache_dir / filename

    expected_sha256: str | None = None
    if verify_checksum:
        expected_sha256 = fetch_sha256(version, filename)

    if cache_path.exists() and not force:
        if expected_sha256 is None or sha256_file(cache_path) == expected_sha256:
            print(f"[blender] cache hit: {cache_path}")
            return cache_path, filename
        print(f"[blender] checksum mismatch in cache, re-downloading: {cache_path}")

    print(f"[blender] downloading: {package_url}")
    try:
        download_to_path(package_url, cache_path)
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Download failed for {package_url}: {exc}") from exc

    if expected_sha256 is not None:
        actual_sha256 = sha256_file(cache_path)
        if actual_sha256 != expected_sha256:
            cache_path.unlink(missing_ok=True)
            raise RuntimeError(
                "SHA256 mismatch after download. "
                f"expected={expected_sha256} actual={actual_sha256} file={cache_path}"
            )

    print(f"[blender] cached package: {cache_path}")
    return cache_path, filename


def _safe_extract_tar_xz(archive_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, mode="r:xz") as tar_handle:
        destination_resolved = destination.resolve()
        for member in tar_handle.getmembers():
            member_path = (destination / member.name).resolve()
            if not str(member_path).startswith(str(destination_resolved) + os.sep):
                raise RuntimeError(f"Unsafe tar entry path detected: {member.name}")
        tar_handle.extractall(path=destination)


def install_linux_package(package_path: Path, install_dir: Path) -> Path:
    install_dir.mkdir(parents=True, exist_ok=True)
    _safe_extract_tar_xz(package_path, install_dir)

    extracted_dirs = sorted([path for path in install_dir.glob("blender-*-linux-x64") if path.is_dir()])
    if not extracted_dirs:
        raise RuntimeError(f"No extracted Blender directory found in {install_dir}")

    latest_dir = extracted_dirs[-1]
    blender_binary = latest_dir / "blender"
    if not blender_binary.exists():
        raise RuntimeError(f"Blender binary missing after extraction: {blender_binary}")

    stable_link = install_dir / "blender"
    if stable_link.exists() or stable_link.is_symlink():
        stable_link.unlink()
    stable_link.symlink_to(blender_binary)
    print(f"[blender] installed linux binary: {stable_link} -> {blender_binary}")
    return stable_link


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download Blender 4.2 package with platform detection and cache."
    )
    parser.add_argument(
        "--platform",
        default="auto",
        choices=["auto", "linux-x86-64", "macos-x64"],
        help="Target platform. Default: auto (detect host platform).",
    )
    parser.add_argument(
        "--version",
        default=os.getenv("BLENDER_VERSION", DEFAULT_VERSION),
        help=f"Blender version in Blender4.2 release dir. Default: {DEFAULT_VERSION}",
    )
    parser.add_argument(
        "--cache-dir",
        default=os.getenv("BLENDER_DOWNLOAD_CACHE_DIR", str(DEFAULT_CACHE_DIR)),
        help=f"Directory for cached packages. Default: {DEFAULT_CACHE_DIR}",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-download even if cache exists.",
    )
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="Skip SHA256 verification against release checksum file.",
    )
    parser.add_argument(
        "--install-dir",
        default=None,
        help="Install directory. Only applied for linux-x86-64 tar.xz package.",
    )
    parser.add_argument(
        "--print-path-only",
        action="store_true",
        help="Print only the cached package path.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        target_platform = detect_platform() if args.platform == "auto" else args.platform
        cache_dir = Path(args.cache_dir).expanduser().resolve()
        package_path, filename = ensure_cached_package(
            version=args.version,
            target_platform=target_platform,
            cache_dir=cache_dir,
            force=args.force,
            verify_checksum=not args.skip_verify,
        )

        if args.install_dir:
            install_dir = Path(args.install_dir).expanduser().resolve()
            if target_platform == "linux-x86-64":
                install_linux_package(package_path, install_dir)
            else:
                print(
                    "[blender] install-dir ignored for macos-x64 package. "
                    "Use the downloaded .dmg manually on macOS."
                )

        if args.print_path_only:
            print(str(package_path))
        else:
            print(f"[blender] package={filename}")
            print(f"[blender] path={package_path}")
    except Exception as exc:  # pragma: no cover - CLI error path
        print(f"[blender] error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
