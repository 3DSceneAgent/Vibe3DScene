#!/usr/bin/env python3
"""Reachability checks for Sketchfab and PolyHaven endpoints."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse

import requests

DEFAULT_TIMEOUT_SECONDS = 8.0
DEFAULT_POLYHAVEN_ASSET_ID = "rock_wall"
DEFAULT_SKETCHFAB_QUERY = "chair"
DEFAULT_USER_AGENT = "3DSceneAgent-reachability-check/1.0"
DEFAULT_HTTP_RETRIES = 3
DEFAULT_RETRY_DELAY_SECONDS = 0.3
PROXY_ENV_NAMES = (
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "ALL_PROXY",
    "https_proxy",
    "http_proxy",
    "all_proxy",
)


@dataclass(frozen=True)
class HttpCheck:
    name: str
    url: str
    description: str
    params: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    expected_statuses: tuple[int, ...] = (200,)


@dataclass
class CheckResult:
    name: str
    kind: str
    target: str
    ok: bool | None
    duration_ms: float
    detail: str
    metadata: dict[str, Any] = field(default_factory=dict)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check Sketchfab and PolyHaven web/API reachability.",
    )
    parser.add_argument(
        "--only",
        choices=("all", "sketchfab", "polyhaven"),
        default="all",
        help="Limit checks to one provider.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Per-check timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS}).",
    )
    parser.add_argument(
        "--sketchfab-api-key",
        default=os.getenv("SKETCHFAB_API_KEY", "").strip(),
        help="Optional Sketchfab API key. Defaults to SKETCHFAB_API_KEY.",
    )
    parser.add_argument(
        "--sketchfab-model-uid",
        default="",
        help="Optional Sketchfab model uid for download metadata probe.",
    )
    parser.add_argument(
        "--polyhaven-asset-id",
        default=DEFAULT_POLYHAVEN_ASSET_ID,
        help=f"PolyHaven asset id for files probe (default: {DEFAULT_POLYHAVEN_ASSET_ID}).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON output.",
    )
    parser.add_argument(
        "--http-retries",
        type=int,
        default=DEFAULT_HTTP_RETRIES,
        help=f"HTTP retry attempts per endpoint (default: {DEFAULT_HTTP_RETRIES}).",
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=DEFAULT_RETRY_DELAY_SECONDS,
        help=f"Delay between HTTP retries in seconds (default: {DEFAULT_RETRY_DELAY_SECONDS}).",
    )
    return parser.parse_args()


def build_sketchfab_checks(api_key: str, model_uid: str) -> tuple[list[HttpCheck], list[CheckResult]]:
    checks = [
        HttpCheck(
            name="sketchfab-web",
            url="https://sketchfab.com/",
            description="Sketchfab website homepage",
        ),
        HttpCheck(
            name="sketchfab-api-search",
            url="https://api.sketchfab.com/v3/search",
            description="Sketchfab public search API",
            params={"type": "models", "count": 1, "q": DEFAULT_SKETCHFAB_QUERY},
        ),
    ]
    skipped: list[CheckResult] = []

    if api_key:
        auth_headers = {"Authorization": f"Token {api_key}"}
        checks.append(
            HttpCheck(
                name="sketchfab-api-me",
                url="https://api.sketchfab.com/v3/me",
                description="Sketchfab authenticated profile API",
                headers=auth_headers,
            )
        )
        if model_uid:
            checks.append(
                HttpCheck(
                    name="sketchfab-api-download",
                    url=f"https://api.sketchfab.com/v3/models/{model_uid}/download",
                    description="Sketchfab model download metadata API",
                    headers=auth_headers,
                )
            )
    else:
        skipped.append(
            CheckResult(
                name="sketchfab-api-me",
                kind="http",
                target="https://api.sketchfab.com/v3/me",
                ok=None,
                duration_ms=0.0,
                detail="skipped: provide --sketchfab-api-key or SKETCHFAB_API_KEY to test authenticated access",
            )
        )
        if model_uid:
            skipped.append(
                CheckResult(
                    name="sketchfab-api-download",
                    kind="http",
                    target=f"https://api.sketchfab.com/v3/models/{model_uid}/download",
                    ok=None,
                    duration_ms=0.0,
                    detail="skipped: download probe requires a Sketchfab API key",
                )
            )

    return checks, skipped


def build_polyhaven_checks(asset_id: str) -> tuple[list[HttpCheck], list[CheckResult]]:
    checks = [
        HttpCheck(
            name="polyhaven-web",
            url="https://polyhaven.com/",
            description="PolyHaven website homepage",
        ),
        HttpCheck(
            name="polyhaven-api-assets",
            url="https://api.polyhaven.com/assets",
            description="PolyHaven asset listing API",
        ),
        HttpCheck(
            name="polyhaven-api-files",
            url=f"https://api.polyhaven.com/files/{asset_id}",
            description="PolyHaven asset files API",
        ),
    ]
    return checks, []


def collect_host_targets(checks: list[HttpCheck]) -> list[tuple[str, int]]:
    targets: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for check in checks:
        parsed = urlparse(check.url)
        host = parsed.hostname
        if not host:
            continue
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        target = (host, port)
        if target not in seen:
            targets.append(target)
            seen.add(target)
    return targets


def _status_label(ok: bool | None) -> str:
    if ok is True:
        return "PASS"
    if ok is False:
        return "FAIL"
    return "SKIP"


def _format_duration(duration_ms: float) -> str:
    return f"{duration_ms:.1f}ms"


def _active_proxy_env() -> dict[str, str]:
    return {
        name: value
        for name in PROXY_ENV_NAMES
        if (value := os.getenv(name))
    }


def _probe_dns(host: str, port: int) -> CheckResult:
    start = time.perf_counter()
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addresses: list[str] = []
        seen: set[str] = set()
        for info in infos:
            address = info[4][0]
            if address not in seen:
                addresses.append(address)
                seen.add(address)
        duration_ms = (time.perf_counter() - start) * 1000
        preview = ", ".join(addresses[:4])
        if len(addresses) > 4:
            preview += ", ..."
        return CheckResult(
            name=f"dns:{host}",
            kind="dns",
            target=host,
            ok=True,
            duration_ms=duration_ms,
            detail=f"resolved {len(addresses)} address(es): {preview}",
            metadata={"addresses": addresses},
        )
    except Exception as exc:
        duration_ms = (time.perf_counter() - start) * 1000
        return CheckResult(
            name=f"dns:{host}",
            kind="dns",
            target=host,
            ok=False,
            duration_ms=duration_ms,
            detail=f"{type(exc).__name__}: {exc}",
        )


def _probe_tcp(host: str, port: int, timeout: float) -> CheckResult:
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout) as connection:
            peer = connection.getpeername()
        duration_ms = (time.perf_counter() - start) * 1000
        return CheckResult(
            name=f"tcp:{host}:{port}",
            kind="tcp",
            target=f"{host}:{port}",
            ok=True,
            duration_ms=duration_ms,
            detail=f"connected to {peer[0]}:{peer[1]}",
            metadata={"peer": [peer[0], peer[1]]},
        )
    except Exception as exc:
        duration_ms = (time.perf_counter() - start) * 1000
        return CheckResult(
            name=f"tcp:{host}:{port}",
            kind="tcp",
            target=f"{host}:{port}",
            ok=False,
            duration_ms=duration_ms,
            detail=f"{type(exc).__name__}: {exc}",
        )


def _probe_http(check: HttpCheck, timeout: float, retries: int, retry_delay: float) -> CheckResult:
    start = time.perf_counter()
    request_headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Connection": "close",
        **check.headers,
    }
    attempts = max(1, retries)
    last_detail = "unknown failure"
    last_metadata = {"description": check.description}

    for attempt in range(1, attempts + 1):
        response: requests.Response | None = None
        try:
            response = requests.get(
                check.url,
                params=check.params or None,
                headers=request_headers,
                timeout=timeout,
                allow_redirects=True,
            )
            detail = f"status={response.status_code} final_url={response.url}"
            metadata = {
                "description": check.description,
                "status_code": response.status_code,
                "final_url": response.url,
                "attempt": attempt,
            }
            if response.history:
                detail += f" redirects={len(response.history)}"
            if content_length := response.headers.get("Content-Length"):
                detail += f" content_length={content_length}"

            if response.status_code in check.expected_statuses:
                duration_ms = (time.perf_counter() - start) * 1000
                if attempt > 1:
                    detail += f" attempts={attempt}"
                return CheckResult(
                    name=check.name,
                    kind="http",
                    target=check.url,
                    ok=True,
                    duration_ms=duration_ms,
                    detail=detail,
                    metadata=metadata,
                )

            last_detail = detail
            last_metadata = metadata
            if 400 <= response.status_code < 500:
                break
        except Exception as exc:
            last_detail = f"{type(exc).__name__}: {exc}"
            last_metadata = {
                "description": check.description,
                "attempt": attempt,
                "error_type": type(exc).__name__,
            }
        finally:
            if response is not None:
                response.close()

        if attempt < attempts and retry_delay > 0:
            time.sleep(retry_delay)

    duration_ms = (time.perf_counter() - start) * 1000
    if attempts > 1:
        last_detail = f"{last_detail} attempts={attempts}"
    return CheckResult(
        name=check.name,
        kind="http",
        target=check.url,
        ok=False,
        duration_ms=duration_ms,
        detail=last_detail,
        metadata=last_metadata,
    )


def summarize_results(results: list[CheckResult]) -> dict[str, int]:
    passed = sum(result.ok is True for result in results)
    failed = sum(result.ok is False for result in results)
    skipped = sum(result.ok is None for result in results)
    return {
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "total": len(results),
    }


def run_checks(args: argparse.Namespace) -> list[CheckResult]:
    checks: list[HttpCheck] = []
    skipped: list[CheckResult] = []

    if args.only in {"all", "sketchfab"}:
        sketchfab_checks, sketchfab_skipped = build_sketchfab_checks(
            api_key=args.sketchfab_api_key.strip(),
            model_uid=args.sketchfab_model_uid.strip(),
        )
        checks.extend(sketchfab_checks)
        skipped.extend(sketchfab_skipped)

    if args.only in {"all", "polyhaven"}:
        polyhaven_checks, polyhaven_skipped = build_polyhaven_checks(
            asset_id=args.polyhaven_asset_id.strip(),
        )
        checks.extend(polyhaven_checks)
        skipped.extend(polyhaven_skipped)

    results: list[CheckResult] = []
    for host, port in collect_host_targets(checks):
        results.append(_probe_dns(host, port))
        results.append(_probe_tcp(host, port, args.timeout))

    for check in checks:
        results.append(_probe_http(check, args.timeout, args.http_retries, args.retry_delay))

    results.extend(skipped)
    return results


def render_text_output(results: list[CheckResult], summary: dict[str, int]) -> None:
    proxies = _active_proxy_env()
    if proxies:
        print(f"[info] proxy env detected: {', '.join(sorted(proxies))}")
    else:
        print("[info] proxy env detected: none")

    for result in results:
        label = _status_label(result.ok)
        print(
            f"[{label}] {result.name} "
            f"({_format_duration(result.duration_ms)}) "
            f"{result.detail}"
        )

    overall_ok = summary["failed"] == 0
    summary_label = "PASS" if overall_ok else "FAIL"
    print(
        f"[{summary_label}] summary "
        f"passed={summary['passed']} failed={summary['failed']} "
        f"skipped={summary['skipped']} total={summary['total']}"
    )


def main() -> int:
    args = parse_args()
    results = run_checks(args)
    summary = summarize_results(results)
    payload = {
        "ok": summary["failed"] == 0,
        "summary": summary,
        "proxy_env": sorted(_active_proxy_env()),
        "results": [asdict(result) for result in results],
    }
    if args.json:
        json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
        print()
    else:
        render_text_output(results, summary)
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
