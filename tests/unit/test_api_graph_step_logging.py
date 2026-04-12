from scene_agent.interfaces.api.shared import extract_graph_step_events


def test_extract_graph_step_events_adds_evaluator_summary() -> None:
    steps = extract_graph_step_events(
        "updates",
        {
            "evaluator": {
                "transition_reason": "todo_still_working",
                "current_todo_stall_count": 3,
                "active_todo_id": "todo-2",
                "verification_result": {
                    "status": "working",
                    "reason": "The van is still missing roof details.",
                },
            }
        },
    )

    assert len(steps) == 1
    step = steps[0]
    assert step["step"] == "evaluator"
    assert step["summary"] == {
        "transition_reason": "todo_still_working",
        "current_todo_stall_count": 3,
        "active_todo_id": "todo-2",
        "verification_status": "working",
        "verification_reason": "The van is still missing roof details.",
    }


def test_extract_graph_step_events_marks_missing_fresh_verification() -> None:
    steps = extract_graph_step_events(
        "updates",
        {
            "evaluator": {
                "transition_reason": "todo_waiting_for_fresh_verification",
                "current_todo_stall_count": 5,
                "verification_result": None,
                "todo_versions": [{"todo_id": "todo-1", "status": "skipped"}],
            }
        },
    )

    assert len(steps) == 1
    assert steps[0]["summary"] == {
        "transition_reason": "todo_waiting_for_fresh_verification",
        "current_todo_stall_count": 5,
        "todo_state_changed": True,
        "verification_status": None,
    }
