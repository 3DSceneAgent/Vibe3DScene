"""
Stream payload harness for inspecting /chat/stream SSE events.

Usage:
  python scripts/stream_payload_harness.py --message "Hello" --thread-id demo
"""
from __future__ import annotations

import argparse
import json
from typing import Iterator

import requests


def iter_sse_payloads(response: requests.Response) -> Iterator[str]:
    for line in response.iter_lines():
        if not line:
            continue
        decoded = line.decode("utf-8")
        if not decoded.startswith("data:"):
            continue
        yield decoded.replace("data:", "", 1).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect streaming payloads from /chat/stream.")
    parser.add_argument("--base-url", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--message", required=True, help="Prompt to send")
    parser.add_argument("--thread-id", default="stream-harness", help="Thread ID to use")
    parser.add_argument("--max-events", type=int, default=50, help="Stop after N events")
    args = parser.parse_args()

    response = requests.post(
        f"{args.base_url}/chat/stream",
        json={"message": args.message, "thread_id": args.thread_id},
        stream=True,
        timeout=120,
    )
    response.raise_for_status()

    print(f"Streaming from {args.base_url}/chat/stream (thread_id={args.thread_id})")
    count = 0
    for payload in iter_sse_payloads(response):
        count += 1
        try:
            parsed = json.loads(payload)
            print(json.dumps(parsed, ensure_ascii=False, indent=2))
        except json.JSONDecodeError:
            print(payload)
        if count >= args.max_events:
            break

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
