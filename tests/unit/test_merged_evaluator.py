from scene_agent.agent.nodes import evaluator_node, finalize_node


def _todo(todo_id: str, description: str, status: str) -> dict:
    return {
        "id": todo_id,
        "description": description,
        "status": status,
        "created_at": "2026-01-01T00:00:00",
        "completed_at": None,
    }


def test_direct_mode_stall_finalizes_after_threshold():
    result = evaluator_node(
        {
            "routed_to_plan": False,
            "request_tool_batches": 2,
            "overall_stall_count": 5,
            "workflow_topology": "single_agent",
            "verification_result": {
                "status": "working",
                "reason": "Not done yet.",
                "edit_suggestions": [],
            },
        }
    )
    assert result["transition_next"] == "finalize"
    assert result["transition_reason"] == "direct_stall_finalize"


def test_evaluator_treats_working_verification_as_regular_stall():
    result = evaluator_node(
        {
            "routed_to_plan": True,
            "request_tool_batches": 2,
            "workflow_topology": "dual_agent",
            "active_todo_id": "todo-1",
            "current_todo_stall_count": 0,
            "overall_stall_count": 3,
            "todos": [_todo("todo-1", "Repair layout", "in_progress")],
            "verification_result": {
                "status": "working",
                "reason": "Scene exploded after the last edit.",
                "edit_suggestions": [],
            },
        }
    )
    assert result["current_todo_stall_count"] == 1
    assert result["transition_next"] == "builder_agent"
    assert result["transition_reason"] == "todo_still_working"


def test_evaluator_does_not_increment_direct_stall_without_fresh_verification():
    result = evaluator_node(
        {
            "routed_to_plan": False,
            "request_tool_batches": 2,
            "overall_stall_count": 4,
            "workflow_topology": "single_agent",
            "verification_result": None,
        }
    )
    assert result["overall_stall_count"] == 4
    assert result["transition_next"] == "agent"
    assert result["transition_reason"] == "direct_waiting_for_fresh_verification"


def test_evaluator_finalizes_when_last_todo_is_skipped():
    state = {
        "task_mode": "plan_mode",
        "routed_to_plan": True,
        "request_tool_batches": 1,
        "workflow_topology": "single_agent",
        "active_todo_id": "todo-1",
        "current_todo_stall_count": 5,
        "todos": [_todo("todo-1", "Adjust chair", "in_progress")],
        "verification_result": {
            "status": "working",
            "reason": "Chair is still misaligned.",
            "edit_suggestions": [],
        },
        "messages": [],
    }
    result = evaluator_node(state)
    assert result["transition_next"] == "finalize"
    assert result["transition_reason"] == "all_todos_terminal_after_skip"
    assert result["todos"][0]["status"] == "skipped"

    summary = finalize_node({**state, **result})
    assert "skipped" in summary["messages"][0].content.lower()
