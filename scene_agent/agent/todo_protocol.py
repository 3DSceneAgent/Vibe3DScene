"""Structured todo protocol used by the agent runtime."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


TODO_UPDATE_TOOL_NAME = "todo_update"
TODO_ACTIVE_STATUSES: frozenset[str] = frozenset({"pending", "in_progress"})
TODO_TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed", "superseded"})
TODO_MUTABLE_STATUSES: frozenset[str] = TODO_ACTIVE_STATUSES | TODO_TERMINAL_STATUSES


class TodoActionModel(BaseModel):
    action: Literal["create", "revise", "set_status", "supersede"]
    todo_id: str | None = None
    title: str | None = None
    status: Literal["pending", "in_progress", "completed", "failed"] | None = None
    reason: str = ""
    set_active: bool = False

    @model_validator(mode="after")
    def validate_payload(self) -> "TodoActionModel":
        if self.action == "create":
            if self.todo_id:
                raise ValueError("create action must not include todo_id")
            if not isinstance(self.title, str) or not self.title.strip():
                raise ValueError("create action requires a non-empty title")
            if self.status not in {"pending", "in_progress", None}:
                raise ValueError("create action only supports pending or in_progress status")
        elif self.action == "revise":
            if not isinstance(self.todo_id, str) or not self.todo_id.strip():
                raise ValueError("revise action requires todo_id")
            if not isinstance(self.title, str) or not self.title.strip():
                raise ValueError("revise action requires a non-empty title")
            if self.status is not None:
                raise ValueError("revise action must not include status")
        elif self.action == "set_status":
            if not isinstance(self.todo_id, str) or not self.todo_id.strip():
                raise ValueError("set_status action requires todo_id")
            if self.status not in {"pending", "in_progress", "completed", "failed"}:
                raise ValueError("set_status action requires a valid status")
            if self.title is not None:
                raise ValueError("set_status action must not include title")
        elif self.action == "supersede":
            if not isinstance(self.todo_id, str) or not self.todo_id.strip():
                raise ValueError("supersede action requires todo_id")
            if self.title is not None or self.status is not None:
                raise ValueError("supersede action must not include title or status")
        return self


class TodoUpdateRequest(BaseModel):
    actions: list[TodoActionModel] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_actions(self) -> "TodoUpdateRequest":
        if not self.actions:
            raise ValueError("todo_update requires at least one action")
        return self


def parse_todo_update_request(raw_args: Any) -> TodoUpdateRequest:
    payload = raw_args
    if isinstance(raw_args, str):
        payload = json.loads(raw_args)
    if not isinstance(payload, dict):
        raise ValueError("todo_update args must be a JSON object")
    return TodoUpdateRequest.model_validate(payload)
