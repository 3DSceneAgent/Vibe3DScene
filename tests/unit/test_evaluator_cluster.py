from scene_agent.agent.nodes import evaluator_node


def _todo(todo_id: str, description: str, status: str) -> dict:
    return {
        "id": todo_id,
        "description": description,
        "status": status,
        "created_at": "2026-01-01T00:00:00",
        "completed_at": None,
    }


def test_evaluator_pure_qa_routes_to_end_without_finalize():
    result = evaluator_node(
        {
            "routed_to_plan": False,
            "request_tool_batches": 0,
            "todos": [],
            "verification_result": None,
        }
    )
    assert result["transition_next"] == "__end__"
    assert result["transition_reason"] == "pure_qa"


def test_evaluator_marks_active_todo_completed_on_done():
    result = evaluator_node(
        {
            "routed_to_plan": True,
            "request_tool_batches": 1,
            "workflow_topology": "single_agent",
            "active_todo_id": "todo-1",
            "todos": [_todo("todo-1", "Place sofa", "in_progress")],
            "verification_result": {
                "status": "done",
                "reason": "Sofa placement looks correct.",
                "edit_suggestions": [],
            },
        }
    )
    assert result["todos"][0]["status"] == "completed"
    assert result["transition_next"] == "finalize"


def test_evaluator_skips_stalled_todo_after_threshold():
    result = evaluator_node(
        {
            "routed_to_plan": True,
            "request_tool_batches": 1,
            "workflow_topology": "single_agent",
            "active_todo_id": "todo-1",
            "current_todo_stall_count": 4,
            "todos": [
                _todo("todo-1", "Adjust chair", "in_progress"),
                _todo("todo-2", "Add lamp", "pending"),
            ],
            "verification_result": {
                "status": "working",
                "reason": "Chair position still off.",
                "edit_suggestions": [],
            },
        }
    )
    todo_by_id = {todo["id"]: todo for todo in result["todos"]}
    assert todo_by_id["todo-1"]["status"] == "skipped"
    assert result["transition_next"] == "agent"


def test_evaluator_does_not_increment_todo_stall_without_fresh_verification():
    result = evaluator_node(
        {
            "routed_to_plan": True,
            "request_tool_batches": 1,
            "workflow_topology": "single_agent",
            "active_todo_id": "todo-1",
            "current_todo_stall_count": 4,
            "todos": [_todo("todo-1", "Adjust chair", "in_progress")],
            "verification_result": None,
        }
    )
    assert result["current_todo_stall_count"] == 4
    assert result["transition_next"] == "agent"
    assert result["transition_reason"] == "todo_waiting_for_fresh_verification"


def test_evaluator_routes_dual_stall_to_planner_refresh():
    result = evaluator_node(
        {
            "routed_to_plan": True,
            "request_tool_batches": 2,
            "workflow_topology": "dual_agent",
            "active_todo_id": "todo-1",
            "current_todo_stall_count": 1,
            "plan_replan_count": 0,
            "max_plan_replans": 3,
            "todos": [_todo("todo-1", "Fix scale", "in_progress")],
            "verification_result": {
                "status": "working",
                "reason": "Scale still mismatched.",
                "edit_suggestions": ["Shrink chair by 15%"],
            },
        }
    )
    assert result["transition_next"] == "planner_refresh"


def test_evaluator_uses_verification_result_only_not_messages():
    result = evaluator_node(
        {
            "routed_to_plan": False,
            "request_tool_batches": 1,
            "workflow_topology": "single_agent",
            "messages": [
                # Should be ignored by evaluator contract.
                {"name": "verification", "content": {"status": "done"}}
            ],
            "verification_result": {
                "status": "working",
                "reason": "Still not complete.",
                "edit_suggestions": [],
            },
        }
    )
    assert result["transition_next"] == "agent"
