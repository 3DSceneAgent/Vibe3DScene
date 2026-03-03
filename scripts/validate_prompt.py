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

_TOPOLOGY_RUN_CHOICES = ("auto", "single", "dual", "compare")
_MEMORY_PROFILE_CHOICES = ("auto", "thread_shared_only", "shared_plus_role_private")


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


def _requested_topology_from_run(topology_run: str) -> str:
    if topology_run == "single":
        return "single_agent"
    if topology_run == "dual":
        return "dual_agent"
    return "auto"


def _topology_mismatch(
    requested_topology: str,
    effective_topology: str | None,
) -> bool | None:
    if requested_topology not in {"single_agent", "dual_agent"}:
        return None
    if not effective_topology:
        return None
    return effective_topology != requested_topology


def _resolve_scenario_thread_id(
    args: argparse.Namespace,
    *,
    scenario_label: str,
) -> str:
    if args.topology_run == "compare":
        if args.thread_id:
            return f"{args.thread_id}__{scenario_label}"
        prefix = "invoke" if args.mode == "invoke" else "api"
        return f"{prefix}-{scenario_label}-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    if args.thread_id:
        return args.thread_id
    prefix = "invoke" if args.mode == "invoke" else "api"
    return f"{prefix}-{scenario_label}-{int(time.time())}-{uuid.uuid4().hex[:6]}"


def _build_compare_summary(
    single_run: dict[str, Any],
    dual_run: dict[str, Any],
) -> dict[str, Any]:
    single_elapsed = single_run.get("elapsed_sec")
    dual_elapsed = dual_run.get("elapsed_sec")
    elapsed_delta: float | None = None
    if isinstance(single_elapsed, (int, float)) and isinstance(dual_elapsed, (int, float)):
        elapsed_delta = round(float(dual_elapsed) - float(single_elapsed), 2)
    return {
        "single_effective_topology": single_run.get("effective_topology"),
        "dual_effective_topology": dual_run.get("effective_topology"),
        "single_effective_task_mode": single_run.get("effective_task_mode"),
        "dual_effective_task_mode": dual_run.get("effective_task_mode"),
        "single_elapsed_sec": single_elapsed,
        "dual_elapsed_sec": dual_elapsed,
        "elapsed_delta_sec": elapsed_delta,
        "single_graph_steps": single_run.get("graph_steps"),
        "dual_graph_steps": dual_run.get("graph_steps"),
        "single_topology_mismatch": single_run.get("topology_mismatch"),
        "dual_topology_mismatch": dual_run.get("topology_mismatch"),
    }


async def _run_invoke_mode(
    args: argparse.Namespace,
    *,
    scenario_label: str,
    thread_id: str,
    requested_topology: str,
    requested_memory_profile: str,
) -> dict[str, Any]:
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
    invoke_input: dict[str, Any] = {
        "messages": [HumanMessage(content=args.prompt)],
        "thread_id": thread_id,
        "workflow_topology_request": requested_topology,
        "memory_profile_request": requested_memory_profile,
    }
    stream = graph.astream(
        invoke_input,
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
    effective_task_mode_raw = values.get("task_mode")
    effective_topology_raw = values.get("workflow_topology")
    effective_memory_profile_raw = values.get("memory_profile")
    effective_task_mode = (
        effective_task_mode_raw
        if isinstance(effective_task_mode_raw, str) and effective_task_mode_raw.strip()
        else None
    )
    effective_topology = (
        effective_topology_raw
        if isinstance(effective_topology_raw, str) and effective_topology_raw.strip()
        else None
    )
    effective_memory_profile = (
        effective_memory_profile_raw
        if isinstance(effective_memory_profile_raw, str) and effective_memory_profile_raw.strip()
        else None
    )

    return {
        "mode": "invoke",
        "run_label": scenario_label,
        "thread_id": thread_id,
        "requested_topology": requested_topology,
        "requested_memory_profile": requested_memory_profile,
        "effective_task_mode": effective_task_mode,
        "effective_topology": effective_topology,
        "effective_memory_profile": effective_memory_profile,
        "topology_mismatch": _topology_mismatch(requested_topology, effective_topology),
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


def _run_api_mode(
    args: argparse.Namespace,
    *,
    scenario_label: str,
    thread_id: str,
    requested_topology: str,
    requested_memory_profile: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"message": args.prompt, "thread_id": thread_id}
    if args.vlm_provider:
        payload["vlm_provider"] = args.vlm_provider
    if args.vlm_model:
        payload["vlm_model"] = args.vlm_model
    payload["workflow_topology"] = requested_topology
    payload["memory_profile"] = requested_memory_profile

    graph_nodes: list[str] = []
    latest_todos: list[dict[str, Any]] = []
    verification_payloads: list[dict[str, Any]] = []
    assistant_text_parts: list[str] = []
    done_event: dict[str, Any] | None = None
    error_event: dict[str, Any] | None = None
    effective_task_mode: str | None = None
    effective_topology: str | None = None
    effective_memory_profile: str | None = None

    start = time.time()
    events = _iter_api_sse(args.base_url, payload, args.timeout)
    for event in events:
        if event.get("event") == "graph_node":
            graph_node_payload = event.get("graph_node")
            node = (graph_node_payload or {}).get("node") if isinstance(graph_node_payload, dict) else None
            if isinstance(node, str):
                graph_nodes.append(node)
            if isinstance(graph_node_payload, dict):
                state_patch = graph_node_payload.get("state_patch")
                if isinstance(state_patch, dict):
                    task_mode_raw = state_patch.get("task_mode")
                    topology_raw = state_patch.get("workflow_topology")
                    memory_profile_raw = state_patch.get("memory_profile")
                    if isinstance(task_mode_raw, str) and task_mode_raw.strip():
                        effective_task_mode = task_mode_raw
                    if isinstance(topology_raw, str) and topology_raw.strip():
                        effective_topology = topology_raw
                    if isinstance(memory_profile_raw, str) and memory_profile_raw.strip():
                        effective_memory_profile = memory_profile_raw
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
        "run_label": scenario_label,
        "thread_id": thread_id,
        "requested_topology": requested_topology,
        "requested_memory_profile": requested_memory_profile,
        "effective_task_mode": effective_task_mode,
        "effective_topology": effective_topology,
        "effective_memory_profile": effective_memory_profile,
        "topology_mismatch": _topology_mismatch(requested_topology, effective_topology),
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
    parser.add_argument(
        "--topology-run",
        choices=_TOPOLOGY_RUN_CHOICES,
        default="auto",
        help="Workflow topology scenario: auto, single, dual, or compare (single+dual).",
    )
    parser.add_argument(
        "--memory-profile",
        choices=_MEMORY_PROFILE_CHOICES,
        default="auto",
        help="Memory profile request to pass through router.",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    load_project_dotenv()
    _set_proxy_env(args.proxy)

    try:
        if args.topology_run == "compare":
            run_summaries: list[dict[str, Any]] = []
            for scenario_label in ("single", "dual"):
                thread_id = _resolve_scenario_thread_id(args, scenario_label=scenario_label)
                requested_topology = _requested_topology_from_run(scenario_label)
                requested_memory_profile = args.memory_profile
                if args.mode == "invoke":
                    run_summary = asyncio.run(
                        _run_invoke_mode(
                            args,
                            scenario_label=scenario_label,
                            thread_id=thread_id,
                            requested_topology=requested_topology,
                            requested_memory_profile=requested_memory_profile,
                        )
                    )
                else:
                    run_summary = _run_api_mode(
                        args,
                        scenario_label=scenario_label,
                        thread_id=thread_id,
                        requested_topology=requested_topology,
                        requested_memory_profile=requested_memory_profile,
                    )
                run_summaries.append(run_summary)
            summary = {
                "mode": args.mode,
                "topology_run": args.topology_run,
                "runs": run_summaries,
                "comparison": _build_compare_summary(run_summaries[0], run_summaries[1]),
            }
        else:
            scenario_label = args.topology_run
            thread_id = _resolve_scenario_thread_id(args, scenario_label=scenario_label)
            requested_topology = _requested_topology_from_run(scenario_label)
            requested_memory_profile = args.memory_profile
            if args.mode == "invoke":
                summary = asyncio.run(
                    _run_invoke_mode(
                        args,
                        scenario_label=scenario_label,
                        thread_id=thread_id,
                        requested_topology=requested_topology,
                        requested_memory_profile=requested_memory_profile,
                    )
                )
            else:
                summary = _run_api_mode(
                    args,
                    scenario_label=scenario_label,
                    thread_id=thread_id,
                    requested_topology=requested_topology,
                    requested_memory_profile=requested_memory_profile,
                )
            summary["topology_run"] = args.topology_run
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
