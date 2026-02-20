from __future__ import annotations

from scene_agent.agent.state import create_todo


def test_create_todo_sets_completed_at_for_completed_status():
    todo = create_todo("Import dragon model", status="completed")
    assert todo["status"] == "completed"
    assert isinstance(todo["completed_at"], str) and todo["completed_at"]


def test_create_todo_keeps_completed_at_none_for_non_completed_status():
    todo = create_todo("Import dragon model", status="pending")
    assert todo["status"] == "pending"
    assert todo["completed_at"] is None
