from scene_agent.agent.todo_state import apply_todo_actions


def test_apply_todo_actions_appends_versions_for_revise_and_status_updates():
    todo_versions, todos, active_todo_id = apply_todo_actions(
        None,
        [
            {
                "action": "create",
                "title": "Import table asset",
                "status": "pending",
                "reason": "Initial plan",
                "set_active": True,
            }
        ],
        source="agent_commit",
        role="general",
    )

    todo_id = todos[0]["id"]
    assert active_todo_id == todo_id
    assert len(todo_versions) == 1
    assert todos[0]["description"] == "Import table asset"

    todo_versions, todos, active_todo_id = apply_todo_actions(
        todo_versions,
        [
            {
                "action": "revise",
                "todo_id": todo_id,
                "title": "Import large wooden dining table asset",
                "reason": "Refined asset target",
            },
            {
                "action": "set_status",
                "todo_id": todo_id,
                "status": "in_progress",
                "reason": "Import started",
                "set_active": True,
            },
        ],
        source="agent_commit",
        role="general",
        previous_active_todo_id=active_todo_id,
    )

    assert len(todo_versions) == 3
    assert todos[0]["id"] == todo_id
    assert todos[0]["description"] == "Import large wooden dining table asset"
    assert todos[0]["status"] == "in_progress"
    assert active_todo_id == todo_id
