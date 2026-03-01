"""Immutable todo state helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from scene_agent.agent.state import TodoItem, TodoVersion
from scene_agent.agent.todo_protocol import TODO_ACTIVE_STATUSES, TodoActionModel


def _coerce_snapshot_todos(raw: Any) -> list[TodoItem]:
    if not isinstance(raw, list):
        return []
    todos: list[TodoItem] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        todo_id = item.get("id")
        description = item.get("description")
        status = item.get("status")
        created_at = item.get("created_at")
        completed_at = item.get("completed_at")
        if not (
            isinstance(todo_id, str)
            and todo_id
            and isinstance(description, str)
            and description
            and isinstance(status, str)
            and status
            and isinstance(created_at, str)
            and created_at
            and (isinstance(completed_at, str) or completed_at is None)
        ):
            continue
        todos.append(
            TodoItem(
                id=todo_id,
                description=description,
                status=status,
                created_at=created_at,
                completed_at=completed_at,
            )
        )
    return todos


def coerce_todo_versions(raw: Any) -> list[TodoVersion]:
    if not isinstance(raw, list):
        return []
    versions: list[TodoVersion] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        required = (
            "event_id",
            "todo_id",
            "version",
            "title",
            "status",
            "reason",
            "source",
            "created_at",
            "created_by_role",
        )
        if not all(key in item for key in required):
            continue
        if not (
            isinstance(item.get("event_id"), str)
            and item["event_id"]
            and isinstance(item.get("todo_id"), str)
            and item["todo_id"]
            and isinstance(item.get("version"), int)
            and item["version"] >= 1
            and isinstance(item.get("title"), str)
            and item["title"]
            and isinstance(item.get("status"), str)
            and item["status"]
            and isinstance(item.get("reason"), str)
            and isinstance(item.get("source"), str)
            and item["source"]
            and isinstance(item.get("created_at"), str)
            and item["created_at"]
            and isinstance(item.get("created_by_role"), str)
            and item["created_by_role"]
            and (isinstance(item.get("prev_event_id"), str) or item.get("prev_event_id") is None)
            and (isinstance(item.get("render_path"), str) or item.get("render_path") is None)
        ):
            continue
        versions.append(TodoVersion(**item))
    return versions


def bootstrap_todo_versions(existing_todos_raw: Any) -> list[TodoVersion]:
    versions: list[TodoVersion] = []
    for todo in _coerce_snapshot_todos(existing_todos_raw):
        versions.append(
            TodoVersion(
                event_id=f"todo_evt_{uuid4().hex}",
                todo_id=todo["id"],
                version=1,
                prev_event_id=None,
                title=todo["description"],
                status=todo["status"],
                reason="bootstrap_from_snapshot",
                source="system",
                created_at=todo["created_at"],
                created_by_role="system",
                render_path=None,
            )
        )
    return versions


def latest_todo_versions(versions_raw: Any, *, fallback_todos_raw: Any = None) -> dict[str, TodoVersion]:
    versions = coerce_todo_versions(versions_raw)
    if not versions and fallback_todos_raw is not None:
        versions = bootstrap_todo_versions(fallback_todos_raw)
    latest: dict[str, TodoVersion] = {}
    for entry in versions:
        latest[entry["todo_id"]] = entry
    return latest


def project_latest_todos(versions_raw: Any, *, fallback_todos_raw: Any = None) -> list[TodoItem]:
    versions = coerce_todo_versions(versions_raw)
    if not versions:
        return _coerce_snapshot_todos(fallback_todos_raw)

    latest: dict[str, TodoVersion] = {}
    first_created_at: dict[str, str] = {}
    for entry in versions:
        todo_id = entry["todo_id"]
        if todo_id not in first_created_at:
            first_created_at[todo_id] = entry["created_at"]
        latest[todo_id] = entry

    projected: list[TodoItem] = []
    for todo_id, entry in latest.items():
        projected.append(
            TodoItem(
                id=todo_id,
                description=entry["title"],
                status=entry["status"],
                created_at=first_created_at.get(todo_id, entry["created_at"]),
                completed_at=entry["created_at"] if entry["status"] == "completed" else None,
            )
        )
    return projected


def derive_active_todo_id(
    *,
    todos: list[TodoItem],
    requested_active_todo_id: str | None = None,
    previous_active_todo_id: str | None = None,
) -> str | None:
    available_ids = {todo["id"] for todo in todos}
    if requested_active_todo_id and requested_active_todo_id in available_ids:
        return requested_active_todo_id

    if previous_active_todo_id and previous_active_todo_id in available_ids:
        for todo in todos:
            if todo["id"] == previous_active_todo_id and todo["status"] in TODO_ACTIVE_STATUSES:
                return previous_active_todo_id

    for preferred_status in ("in_progress", "pending"):
        for todo in todos:
            if todo["status"] == preferred_status:
                return todo["id"]
    return None


def apply_todo_actions(
    existing_versions_raw: Any,
    actions_raw: list[Any],
    *,
    fallback_todos_raw: Any = None,
    source: str,
    role: str,
    render_path: str | None = None,
    previous_active_todo_id: str | None = None,
) -> tuple[list[TodoVersion], list[TodoItem], str | None]:
    versions = coerce_todo_versions(existing_versions_raw)
    if not versions and fallback_todos_raw is not None:
        versions = bootstrap_todo_versions(fallback_todos_raw)

    latest = latest_todo_versions(versions)
    requested_active_todo_id: str | None = None

    for raw_action in actions_raw:
        action = TodoActionModel.model_validate(raw_action)
        if action.action == "create":
            todo_id = f"todo_{uuid4().hex[:12]}"
            previous = None
            title = str(action.title or "").strip()
            status = action.status or "pending"
        else:
            todo_id = str(action.todo_id or "").strip()
            previous = latest.get(todo_id)
            if previous is None:
                raise ValueError(f"Unknown todo_id for {action.action}: {todo_id}")
            title = previous["title"]
            status = previous["status"]

        if action.action == "revise":
            title = str(action.title or "").strip()
        elif action.action == "set_status":
            status = str(action.status or status)
        elif action.action == "supersede":
            status = "superseded"

        next_version = 1 if previous is None else previous["version"] + 1
        entry = TodoVersion(
            event_id=f"todo_evt_{uuid4().hex}",
            todo_id=todo_id,
            version=next_version,
            prev_event_id=previous["event_id"] if previous is not None else None,
            title=title,
            status=status,
            reason=str(action.reason or "").strip(),
            source=source,
            created_at=datetime.now().isoformat(),
            created_by_role=role,
            render_path=render_path,
        )
        versions.append(entry)
        latest[todo_id] = entry
        if action.set_active:
            requested_active_todo_id = todo_id

    todos = project_latest_todos(versions)
    active_todo_id = derive_active_todo_id(
        todos=todos,
        requested_active_todo_id=requested_active_todo_id,
        previous_active_todo_id=previous_active_todo_id,
    )
    return versions, todos, active_todo_id
