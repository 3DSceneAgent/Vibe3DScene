from scene_agent.agent.nodes.shared import build_todo_updates_from_verification


def _todo(todo_id: str, description: str, status: str) -> dict:
    return {
        "id": todo_id,
        "description": description,
        "status": status,
        "created_at": "2026-01-01T00:00:00",
        "completed_at": None,
    }


def test_build_todo_updates_from_verification_targets_todo_id():
    actions, records = build_todo_updates_from_verification(
        {
            "todos": [
                _todo("todo-1", "Import table", "in_progress"),
                _todo("todo-2", "Place chair", "pending"),
            ]
        },
        {
            "todo_assessment": [
                {
                    "todo_id": "todo-2",
                    "status": "done",
                    "reason": "Chair is correctly placed.",
                }
            ]
        },
    )

    assert actions == [
        {
            "action": "set_status",
            "todo_id": "todo-2",
            "status": "completed",
            "reason": "Chair is correctly placed.",
        }
    ]
    assert records[0]["todo_id"] == "todo-2"
