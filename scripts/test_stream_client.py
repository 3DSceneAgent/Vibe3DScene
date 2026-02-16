#!/usr/bin/env python3
"""
Local streaming test client for the 3D Scene Agent headless API.

Usage:
    # Run the API first:
    #   ./scripts/run_headless.sh

    # Then test with a single prompt:
    python scripts/test_stream_client.py --prompt "Create a red cube"

    # Run a predefined example scenario (from example_prompts.md):
    python scripts/test_stream_client.py --example dungeon
    python scripts/test_stream_client.py --example lighting
    python scripts/test_stream_client.py --example camera

    # Run all examples sequentially (each in its own thread):
    python scripts/test_stream_client.py --all

    # Multi-turn conversation:
    python scripts/test_stream_client.py --multi-turn

    # Stress test: create N sessions and verify cleanup:
    python scripts/test_stream_client.py --stress --sessions 5

    # Custom base URL:
    python scripts/test_stream_client.py --base-url http://localhost:8000 --prompt "Hello"
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from typing import Any, Generator

import requests


# ---------------------------------------------------------------------------
# Example scenarios derived from assets/example_prompts.md
# ---------------------------------------------------------------------------

EXAMPLES: dict[str, list[str]] = {
    "dungeon": [
        "Create a low poly scene in a dungeon, with a dragon guarding a pot of gold",
    ],
    "lighting": [
        "Create a simple scene with a few geometric objects, then make the lighting like a studio",
    ],
    "camera": [
        (
            "Create a small scene with a table and some objects on it, "
            "point the camera at the scene, and make it isometric"
        ),
    ],
    "multi_step": [
        "Create a red cube at the origin",
        "Now add a blue sphere next to it",
        "Add a point light above the scene",
        "Render the scene from the default camera",
    ],
}


# ---------------------------------------------------------------------------
# Streaming helpers
# ---------------------------------------------------------------------------

def stream_chat(
    base_url: str,
    thread_id: str,
    message: str,
    *,
    timeout: float = 300.0,
    vlm_provider: str | None = None,
    vlm_model: str | None = None,
) -> Generator[dict[str, Any], None, None]:
    """Send a streaming chat request and yield parsed SSE events."""
    payload: dict[str, Any] = {"message": message, "thread_id": thread_id}
    if vlm_provider:
        payload["vlm_provider"] = vlm_provider
    if vlm_model:
        payload["vlm_model"] = vlm_model

    url = f"{base_url.rstrip('/')}/chat/stream"
    with requests.post(url, json=payload, stream=True, timeout=timeout) as resp:
        resp.raise_for_status()
        buffer = ""
        for chunk in resp.iter_content(chunk_size=None, decode_unicode=True):
            if not chunk:
                continue
            buffer += chunk
            while "\n\n" in buffer:
                event_text, buffer = buffer.split("\n\n", 1)
                lines = [
                    line[len("data:"):].strip()
                    for line in event_text.strip().splitlines()
                    if line.startswith("data:")
                ]
                if not lines:
                    continue
                raw = "\n".join(lines)
                if not raw:
                    continue
                try:
                    parsed = json.loads(raw)
                    yield parsed
                except json.JSONDecodeError:
                    pass


def print_event(event: dict[str, Any], *, verbose: bool = False) -> None:
    """Pretty-print a single stream event."""
    if event.get("error"):
        print(f"  [ERROR] {event['error']}")
        return
    if event.get("event") == "done":
        scene_changed = event.get("scene_has_change", False)
        print(f"  [DONE] scene_has_change={scene_changed}")
        return
    if "delta" in event:
        text = event["delta"]
        print(text, end="", flush=True)
        return
    if event.get("todos"):
        todo_summary = ", ".join(
            f"{t.get('description', '?')}({t.get('status', '?')})"
            for t in event["todos"]
        )
        print(f"\n  [TODOS] {todo_summary}")
        return
    messages = event.get("messages", [])
    for msg in messages:
        msg_type = msg.get("type", "unknown")
        name = msg.get("name") or ""
        if msg_type == "tool":
            content_preview = str(msg.get("content", ""))[:200]
            print(f"\n  [TOOL:{name}] {content_preview}")
        elif verbose:
            content_preview = str(msg.get("content", ""))[:200]
            print(f"\n  [{msg_type}] {content_preview}")


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def health_check(base_url: str) -> dict[str, Any]:
    resp = requests.get(f"{base_url}/health", timeout=5)
    resp.raise_for_status()
    return resp.json()


def list_threads(base_url: str) -> list[str]:
    resp = requests.get(f"{base_url}/threads", timeout=10)
    resp.raise_for_status()
    return resp.json().get("threads", [])


def delete_thread(base_url: str, thread_id: str) -> dict[str, Any]:
    resp = requests.delete(f"{base_url}/threads/{thread_id}", timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_scene(base_url: str, thread_id: str) -> dict[str, Any]:
    resp = requests.get(f"{base_url}/scene/{thread_id}", timeout=30)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Test scenarios
# ---------------------------------------------------------------------------

def new_thread_id(prefix: str = "test") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def run_single_prompt(
    base_url: str,
    prompt: str,
    *,
    thread_id: str | None = None,
    verbose: bool = False,
    vlm_provider: str | None = None,
    vlm_model: str | None = None,
) -> str:
    """Send a single prompt and print the streamed response. Returns thread_id."""
    tid = thread_id or new_thread_id()
    print(f"\n{'='*70}")
    print(f"Thread: {tid}")
    print(f"Prompt: {prompt}")
    print(f"{'='*70}")

    t0 = time.time()
    had_error = False
    for event in stream_chat(
        base_url,
        tid,
        prompt,
        vlm_provider=vlm_provider,
        vlm_model=vlm_model,
    ):
        print_event(event, verbose=verbose)
        if event.get("error"):
            had_error = True
    elapsed = time.time() - t0
    print(f"\n--- elapsed: {elapsed:.1f}s {'(ERROR)' if had_error else '(ok)'} ---\n")
    return tid


def run_example(
    base_url: str,
    name: str,
    *,
    verbose: bool = False,
    vlm_provider: str | None = None,
    vlm_model: str | None = None,
) -> str:
    """Run a named example scenario."""
    prompts = EXAMPLES.get(name)
    if not prompts:
        print(f"Unknown example: {name}. Available: {', '.join(EXAMPLES.keys())}")
        sys.exit(1)

    tid = new_thread_id(f"ex-{name}")
    print(f"\n{'#'*70}")
    print(f"# Example: {name} ({len(prompts)} turn(s))")
    print(f"# Thread:  {tid}")
    print(f"{'#'*70}")

    for i, prompt in enumerate(prompts, 1):
        print(f"\n  [Turn {i}/{len(prompts)}]")
        run_single_prompt(
            base_url,
            prompt,
            thread_id=tid,
            verbose=verbose,
            vlm_provider=vlm_provider,
            vlm_model=vlm_model,
        )
    return tid


def run_multi_turn(
    base_url: str,
    *,
    verbose: bool = False,
    vlm_provider: str | None = None,
    vlm_model: str | None = None,
) -> str:
    """Run a multi-turn conversation that builds a scene incrementally."""
    return run_example(
        base_url,
        "multi_step",
        verbose=verbose,
        vlm_provider=vlm_provider,
        vlm_model=vlm_model,
    )


def run_stress_test(
    base_url: str,
    sessions: int = 5,
    *,
    verbose: bool = False,
    vlm_provider: str | None = None,
    vlm_model: str | None = None,
) -> None:
    """Create N sessions, send a message to each, then delete all of them."""
    print(f"\n{'#'*70}")
    print(f"# Stress test: {sessions} sessions")
    print(f"{'#'*70}")

    thread_ids: list[str] = []
    for i in range(sessions):
        tid = new_thread_id(f"stress-{i}")
        thread_ids.append(tid)
        print(f"\n  [{i+1}/{sessions}] Creating session {tid}")
        try:
            run_single_prompt(
                base_url,
                f"Create a simple cube labeled session-{i}",
                thread_id=tid,
                verbose=verbose,
                vlm_provider=vlm_provider,
                vlm_model=vlm_model,
            )
        except Exception as exc:
            print(f"  [ERROR] Session {tid}: {exc}")

    # List threads
    print("\n--- Active threads before cleanup ---")
    try:
        active = list_threads(base_url)
        print(f"  Count: {len(active)}")
        for t in active[:20]:
            print(f"    {t}")
    except Exception as exc:
        print(f"  [ERROR] {exc}")

    # Delete all test sessions
    print("\n--- Deleting test sessions ---")
    for tid in thread_ids:
        try:
            result = delete_thread(base_url, tid)
            cleaned = result.get("cleaned", [])
            print(f"  [ok] Deleted {tid}: {cleaned}")
        except Exception as exc:
            print(f"  [ERROR] Delete {tid}: {exc}")

    # Verify cleanup
    print("\n--- Active threads after cleanup ---")
    try:
        active = list_threads(base_url)
        remaining_test = [t for t in active if t in set(thread_ids)]
        print(f"  Total: {len(active)}, test sessions remaining: {len(remaining_test)}")
        if remaining_test:
            print(f"  [WARN] Leaked sessions: {remaining_test}")
        else:
            print("  [ok] All test sessions cleaned up")
    except Exception as exc:
        print(f"  [ERROR] {exc}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Local streaming test client for 3D Scene Agent API."
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="API base URL (default: http://localhost:8000)",
    )
    parser.add_argument("--prompt", type=str, help="Send a single prompt")
    parser.add_argument("--thread-id", type=str, help="Thread ID (auto-generated if omitted)")
    parser.add_argument(
        "--example",
        type=str,
        choices=list(EXAMPLES.keys()),
        help="Run a predefined example scenario",
    )
    parser.add_argument("--all", action="store_true", help="Run all example scenarios")
    parser.add_argument("--multi-turn", action="store_true", help="Run multi-turn conversation")
    parser.add_argument("--stress", action="store_true", help="Run stress test (create + delete)")
    parser.add_argument("--sessions", type=int, default=5, help="Number of sessions for stress test")
    parser.add_argument("--delete", type=str, metavar="THREAD_ID", help="Delete a specific thread")
    parser.add_argument("--list-threads", action="store_true", help="List active threads")
    parser.add_argument("--cleanup-all", action="store_true", help="Delete ALL active threads")
    parser.add_argument("--vlm-provider", type=str, help="VLM provider (openai, anthropic, gemini)")
    parser.add_argument("--vlm-model", type=str, help="VLM model name")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base = args.base_url.rstrip("/")

    # Health check first
    try:
        health = health_check(base)
        print(f"[ok] Server healthy: mode={health.get('blender_mode')}, worker={health.get('worker_id')}")
    except Exception as exc:
        print(f"[fail] Server not reachable at {base}: {exc}", file=sys.stderr)
        return 1

    vlm_kw = {"vlm_provider": args.vlm_provider, "vlm_model": args.vlm_model}

    if args.list_threads:
        threads = list_threads(base)
        print(f"Active threads ({len(threads)}):")
        for t in threads:
            print(f"  {t}")
        return 0

    if args.delete:
        result = delete_thread(base, args.delete)
        print(f"Deleted {args.delete}: {result.get('cleaned', [])}")
        return 0

    if args.cleanup_all:
        threads = list_threads(base)
        print(f"Deleting {len(threads)} threads...")
        for t in threads:
            try:
                result = delete_thread(base, t)
                print(f"  [ok] {t}: {result.get('cleaned', [])}")
            except Exception as exc:
                print(f"  [err] {t}: {exc}")
        return 0

    if args.prompt:
        run_single_prompt(
            base, args.prompt, thread_id=args.thread_id, verbose=args.verbose, **vlm_kw
        )
        return 0

    if args.example:
        run_example(base, args.example, verbose=args.verbose, **vlm_kw)
        return 0

    if args.multi_turn:
        run_multi_turn(base, verbose=args.verbose, **vlm_kw)
        return 0

    if args.stress:
        run_stress_test(base, sessions=args.sessions, verbose=args.verbose, **vlm_kw)
        return 0

    if args.all:
        created_threads: list[str] = []
        for name in EXAMPLES:
            try:
                tid = run_example(base, name, verbose=args.verbose, **vlm_kw)
                created_threads.append(tid)
            except Exception as exc:
                print(f"\n[ERROR] Example '{name}' failed: {exc}")

        print(f"\n{'#'*70}")
        print(f"# Finished all examples. Threads created: {len(created_threads)}")
        for tid in created_threads:
            print(f"#   {tid}")
        print(f"# Use --cleanup-all or --delete <id> to clean up.")
        print(f"{'#'*70}")
        return 0

    # Default: interactive mode
    print("\nNo action specified. Use --help to see options.")
    print(f"Quick start examples:")
    print(f"  python scripts/test_stream_client.py --prompt 'Create a red cube'")
    print(f"  python scripts/test_stream_client.py --example dungeon")
    print(f"  python scripts/test_stream_client.py --all")
    print(f"  python scripts/test_stream_client.py --stress --sessions 3")
    print(f"  python scripts/test_stream_client.py --cleanup-all")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
