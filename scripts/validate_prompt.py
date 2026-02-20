#!/usr/bin/env python3
"""
Automated prompt validation for 3D Scene Agent.

Supports two modes:
1) API mode: call /chat/stream and summarize results.
2) Invoke mode: call LangGraph directly (pure Python) and summarize results.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import requests
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scene_agent.agent.graph import create_agent_graph
from scene_agent.env import load_project_dotenv


def _set_proxy_env(proxy: str | None) -> None:
    if not proxy:
        return
    os.environ["http_proxy"] = proxy
    os.environ["https_proxy"] = proxy
    os.environ["HTTP_PROXY"] = proxy
    os.environ["HTTPS_PROXY"] = proxy
    os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
    os.environ.setdefault("no_proxy", "127.0.0.1,localhost")


def _message_content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False, default=str)
    return str(content)


def _latest_verification_from_messages(messages: list[Any]) -> dict[str, Any] | None:
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            continue
        name = getattr(message, "name", None)
        if not isinstance(name, str) or "verification" not in name:
            continue
        content = getattr(message, "content", None)
        if isinstance(content, dict):
            return content
    return None


def _last_assistant_text(messages: list[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            text = _message_content_to_text(getattr(message, "content", ""))
            if text.strip():
                return text.strip()
    return ""


def _normalize_stream_event(event: Any) -> tuple[str | None, Any]:
    if isinstance(event, tuple) and len(event) == 2:
        return event[0], event[1]
    return None, event


def _collect_graph_updates_from_payload(
    payload: Any,
    *,
    graph_nodes: list[str],
    latest_todos: list[dict[str, Any]],
) -> None:
    if not isinstance(payload, dict):
        return
    for node_name, update in payload.items():
        if isinstance(node_name, str) and node_name:
            graph_nodes.append(node_name)
        if not isinstance(update, dict):
            continue
        todos = update.get("todos")
        if isinstance(todos, list):
            latest_todos.clear()
            latest_todos.extend([item for item in todos if isinstance(item, dict)])


async def _run_invoke_mode(args: argparse.Namespace) -> dict[str, Any]:
    thread_id = args.thread_id or f"invoke-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    graph_nodes: list[str] = []
    latest_todos: list[dict[str, Any]] = []

    graph = await create_agent_graph(
        session_id=thread_id,
        provider_name=args.vlm_provider,
        model=args.vlm_model,
        api_key=args.vlm_api_key,
    )
    config = {"configurable": {"thread_id": thread_id}}

    start = time.time()
    stream = graph.astream(
        {
            "messages": [HumanMessage(content=args.prompt)],
            "thread_id": thread_id,
        },
        config=config,
        stream_mode=["updates", "messages", "values"],
    )

    async for event in stream:
        mode, payload = _normalize_stream_event(event)
        if mode == "updates":
            _collect_graph_updates_from_payload(
                payload,
                graph_nodes=graph_nodes,
                latest_todos=latest_todos,
            )

    elapsed = time.time() - start
    state = await graph.aget_state(config)
    values = state.values if hasattr(state, "values") and isinstance(state.values, dict) else {}
    state_messages = values.get("messages", []) if isinstance(values.get("messages"), list) else []
    todos_state = values.get("todos", []) if isinstance(values.get("todos"), list) else []

    last_verification = _latest_verification_from_messages(state_messages)
    assistant_text = _last_assistant_text(state_messages)
    todo_check = values.get("todo_check") if isinstance(values.get("todo_check"), dict) else None

    return {
        "mode": "invoke",
        "thread_id": thread_id,
        "elapsed_sec": round(elapsed, 2),
        "graph_steps": len(graph_nodes),
        "graph_nodes_tail": graph_nodes[-30:],
        "latest_todos_event": latest_todos,
        "todos_state": todos_state,
        "todo_check": todo_check,
        "last_verification": last_verification,
        "assistant_text_tail": assistant_text[-1500:] if assistant_text else "",
    }


def _iter_api_sse(base_url: str, payload: dict[str, Any], timeout: float) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with requests.post(
        f"{base_url.rstrip('/')}/chat/stream",
        json=payload,
        stream=True,
        timeout=timeout,
    ) as response:
        response.raise_for_status()
        buffer = ""
        for chunk in response.iter_content(chunk_size=None, decode_unicode=True):
            if not chunk:
                continue
            buffer += chunk
            while "\n\n" in buffer:
                event_text, buffer = buffer.split("\n\n", 1)
                data_lines = [
                    line[len("data:"):].strip()
                    for line in event_text.splitlines()
                    if line.startswith("data:")
                ]
                if not data_lines:
                    continue
                raw = "\n".join(data_lines)
                if not raw:
                    continue
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                events.append(parsed)
    return events


def _run_api_mode(args: argparse.Namespace) -> dict[str, Any]:
    thread_id = args.thread_id or f"api-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    payload: dict[str, Any] = {"message": args.prompt, "thread_id": thread_id}
    if args.vlm_provider:
        payload["vlm_provider"] = args.vlm_provider
    if args.vlm_model:
        payload["vlm_model"] = args.vlm_model

    graph_nodes: list[str] = []
    latest_todos: list[dict[str, Any]] = []
    verification_payloads: list[dict[str, Any]] = []
    assistant_text_parts: list[str] = []
    done_event: dict[str, Any] | None = None
    error_event: dict[str, Any] | None = None

    start = time.time()
    events = _iter_api_sse(args.base_url, payload, args.timeout)
    for event in events:
        if event.get("event") == "graph_node":
            node = (event.get("graph_node") or {}).get("node")
            if isinstance(node, str):
                graph_nodes.append(node)
        todos = event.get("todos")
        if isinstance(todos, list):
            latest_todos = [item for item in todos if isinstance(item, dict)]
        if "delta" in event and isinstance(event["delta"], str):
            assistant_text_parts.append(event["delta"])
        messages = event.get("messages")
        if isinstance(messages, list):
            for message in messages:
                if not isinstance(message, dict):
                    continue
                message_name = message.get("name")
                if isinstance(message_name, str) and "verification" in message_name:
                    content = message.get("content")
                    if isinstance(content, dict):
                        verification_payloads.append(content)
        if event.get("error"):
            error_event = event
        if event.get("event") == "done":
            done_event = event

    elapsed = time.time() - start
    todos_response = requests.get(f"{args.base_url.rstrip('/')}/todos/{thread_id}", timeout=60)
    todos_state = []
    if todos_response.ok:
        body = todos_response.json()
        if isinstance(body, dict) and isinstance(body.get("todos"), list):
            todos_state = body["todos"]

    assistant_text = "".join(assistant_text_parts).strip()

    return {
        "mode": "api",
        "thread_id": thread_id,
        "elapsed_sec": round(elapsed, 2),
        "done_event": done_event,
        "error_event": error_event,
        "graph_steps": len(graph_nodes),
        "graph_nodes_tail": graph_nodes[-30:],
        "verification_count": len(verification_payloads),
        "last_verification": verification_payloads[-1] if verification_payloads else None,
        "latest_todos_event": latest_todos,
        "todos_state": todos_state,
        "assistant_text_tail": assistant_text[-1500:] if assistant_text else "",
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate a single prompt end-to-end.")
    parser.add_argument(
        "--mode",
        choices=("invoke", "api"),
        default="invoke",
        help="Validation mode: pure invoke or API stream.",
    )
    parser.add_argument("--prompt", required=True, help="Prompt to run.")
    parser.add_argument("--thread-id", default=None, help="Optional fixed thread ID.")
    parser.add_argument("--proxy", default=None, help="Proxy URL, e.g. http://127.0.0.1:7890")
    parser.add_argument("--timeout", type=float, default=1800.0, help="API streaming timeout seconds.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="API base URL (api mode).")
    parser.add_argument("--vlm-provider", default=None, help="Optional VLM provider.")
    parser.add_argument("--vlm-model", default=None, help="Optional VLM model.")
    parser.add_argument("--vlm-api-key", default=None, help="Optional VLM API key for invoke mode.")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    load_project_dotenv()
    _set_proxy_env(args.proxy)

    try:
        if args.mode == "invoke":
            summary = asyncio.run(_run_invoke_mode(args))
        else:
            summary = _run_api_mode(args)
    except Exception as exc:
        print(
            json.dumps(
                {"status": "error", "error": str(exc)},
                ensure_ascii=False,
            )
        )
        return 1

    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
