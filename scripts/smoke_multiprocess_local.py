#!/usr/bin/env python3
"""Smoke checks for local multiprocess gateway + workers."""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any

import requests


def _get_json(url: str, timeout: float) -> tuple[int, dict[str, Any] | None, str]:
    response = requests.get(url, timeout=timeout)
    text = response.text
    payload = None
    try:
        payload = response.json()
    except Exception:
        pass
    return response.status_code, payload, text


def _check_health(name: str, url: str, timeout: float) -> dict[str, Any]:
    status, payload, text = _get_json(url, timeout)
    if status != 200:
        raise RuntimeError(f"{name} health failed: status={status} body={text}")
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise RuntimeError(f"{name} health invalid payload: {text}")
    print(f"[ok] {name} health -> {payload.get('worker_id')}")
    return payload


def _owner_proxy_check(worker1_url: str, worker2_url: str, timeout: float, thread_id: str) -> None:
    endpoint = f"/threads/{thread_id}/reference-images"
    url1 = f"{worker1_url}{endpoint}"
    url2 = f"{worker2_url}{endpoint}"
    r1 = requests.get(url1, timeout=timeout)
    r2 = requests.get(url2, timeout=timeout)
    if r1.status_code != 200 or r2.status_code != 200:
        raise RuntimeError(
            f"owner-proxy status failed: worker1={r1.status_code} worker2={r2.status_code} "
            f"body1={r1.text} body2={r2.text}"
        )
    owner_1 = r1.headers.get("X-Session-Owner")
    owner_2 = r2.headers.get("X-Session-Owner")
    epoch_1 = r1.headers.get("X-Session-Lease-Epoch")
    epoch_2 = r2.headers.get("X-Session-Lease-Epoch")
    if not owner_1 or not owner_2 or owner_1 != owner_2:
        raise RuntimeError(
            f"owner header mismatch: owner1={owner_1!r} owner2={owner_2!r} "
            f"status1={r1.status_code} status2={r2.status_code}"
        )
    if epoch_1 and epoch_2 and epoch_1 != epoch_2:
        raise RuntimeError(f"lease epoch mismatch: epoch1={epoch_1} epoch2={epoch_2}")
    print(f"[ok] owner-proxy owner={owner_1} epoch={epoch_1 or epoch_2}")


def _mcp_tools_check(
    gateway_url: str,
    timeout: float,
    thread_id: str,
    retries: int,
    retry_interval: float,
) -> None:
    url = f"{gateway_url}/threads/{thread_id}/mcp-tools"
    last_error: str = "unknown"
    for attempt in range(1, max(1, retries) + 1):
        r = requests.get(url, timeout=timeout)
        if r.status_code == 200:
            payload = r.json()
            tool_count = int(payload.get("tool_count", 0))
            if tool_count > 0:
                print(f"[ok] mcp-tools loaded={tool_count} (attempt={attempt})")
                return
            last_error = f"empty tools body={r.text}"
        else:
            last_error = f"status={r.status_code} body={r.text}"
        if attempt < retries:
            time.sleep(max(0.0, retry_interval))
    raise RuntimeError(f"mcp-tools failed after {retries} attempts: {last_error}")


def _chat_check(gateway_url: str, timeout: float, thread_id: str) -> None:
    url = f"{gateway_url}/chat"
    payload = {
        "thread_id": thread_id,
        "message": "Reply with one short English sentence only.",
    }
    r = requests.post(url, json=payload, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"chat failed: status={r.status_code} body={r.text}")
    body = r.json()
    response_text = str(body.get("response", "")).strip()
    if not response_text:
        raise RuntimeError(f"chat returned empty response: {r.text}")
    print(f"[ok] chat response={response_text[:120]!r}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test local multiprocess backend.")
    parser.add_argument("--gateway-url", default="http://127.0.0.1:8000")
    parser.add_argument("--worker1-url", default="http://127.0.0.1:18001")
    parser.add_argument("--worker2-url", default="http://127.0.0.1:18002")
    parser.add_argument("--health-timeout", type=float, default=8.0)
    parser.add_argument("--request-timeout", type=float, default=45.0)
    parser.add_argument("--chat-timeout", type=float, default=180.0)
    parser.add_argument("--thread-prefix", default="smoke-local")
    parser.add_argument("--skip-chat", action="store_true")
    parser.add_argument("--mcp-retries", type=int, default=6)
    parser.add_argument("--mcp-retry-interval", type=float, default=3.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    suffix = str(int(time.time()))
    proxy_thread = f"{args.thread_prefix}-proxy-{suffix}"
    chat_thread = f"{args.thread_prefix}-chat-{suffix}"
    try:
        _check_health("gateway", f"{args.gateway_url}/health", args.health_timeout)
        _check_health("worker-1", f"{args.worker1_url}/health", args.health_timeout)
        _check_health("worker-2", f"{args.worker2_url}/health", args.health_timeout)
        _owner_proxy_check(args.worker1_url, args.worker2_url, args.request_timeout, proxy_thread)
        _mcp_tools_check(
            args.gateway_url,
            args.request_timeout,
            chat_thread,
            retries=args.mcp_retries,
            retry_interval=args.mcp_retry_interval,
        )
        if not args.skip_chat:
            _chat_check(args.gateway_url, args.chat_timeout, chat_thread)
        print("[ok] smoke checks passed")
        return 0
    except Exception as exc:
        print(f"[fail] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
