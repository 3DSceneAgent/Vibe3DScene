from scene_agent.agent.convergence import evaluate_convergence
from scene_agent.agent.nodes import evaluator_node


def test_evaluator_emits_guided_retry_on_repeat_loop():
    base_state = {
        "active_todo_id": "todo-1",
        "routed_to_plan": True,
        "request_tool_batches": 1,
        "recent_verification_signatures": [
            {
                "todo_id": "todo-1",
                "status": "mismatch",
                "failure_bucket": "scale",
                "verified_path": "/renders/1.png",
                "reason": "scale mismatch",
            },
            {
                "todo_id": "todo-1",
                "status": "mismatch",
                "failure_bucket": "scale",
                "verified_path": "/renders/2.png",
                "reason": "scale mismatch",
            },
        ],
        "verification_result": {
            "status": "working",
            "reason": "Scale is still off.",
            "edit_suggestions": ["Reduce chair scale"],
        },
        "last_verified_path": "/renders/3.png",
    }

    result = evaluator_node(base_state)
    assert result["convergence_eval"]["status"] == "guided_retry"
    assert result["convergence_eval"]["pattern"] == "repeat_loop"
    assert result["convergence_intervention_count"] == 1
    assert "messages" in result


def test_evaluator_stops_on_convergence_hard_stop():
    result = evaluator_node(
        {
            "routed_to_plan": True,
            "request_tool_batches": 1,
            "active_todo_id": "todo-1",
            "convergence_intervention_count": 2,
            "recent_verification_signatures": [
                {
                    "todo_id": "todo-1",
                    "status": "mismatch",
                    "failure_bucket": "scale",
                    "verified_path": "/renders/1.png",
                    "reason": "scale mismatch",
                },
                {
                    "todo_id": "todo-1",
                    "status": "mismatch",
                    "failure_bucket": "scale",
                    "verified_path": "/renders/2.png",
                    "reason": "scale mismatch",
                },
            ],
            "verification_result": {
                "status": "working",
                "reason": "Scale mismatch again",
                "edit_suggestions": ["Adjust scale"],
            },
            "last_verified_path": "/renders/3.png",
        }
    )

    assert result["transition_next"] == "finalize"
    assert "after_guidance" in result["transition_reason"]


def test_evaluate_convergence_keeps_history_when_verification_is_skipped():
    previous_history = [
        {
            "todo_id": "todo-1",
            "status": "mismatch",
            "failure_bucket": "scale",
            "verified_path": "/renders/1.png",
            "reason": "scale mismatch",
        }
    ]

    result = evaluate_convergence(
        state={
            "recent_verification_signatures": previous_history,
            "convergence_intervention_count": 1,
        },
        verification={},
        quality_status="skipped",
        quality_reason="No fresh verification evidence.",
        active_todo_id="todo-1",
        last_verified_path=None,
    )

    assert result["recent_verification_signatures"] == previous_history
    assert result["convergence_intervention_count"] == 1
    assert result["convergence_eval"]["status"] == "stable"


def test_evaluate_convergence_allows_second_guided_retry_before_hard_stop():
    result = evaluate_convergence(
        state={
            "recent_verification_signatures": [
                {
                    "todo_id": "todo-1",
                    "status": "mismatch",
                    "failure_bucket": "scale",
                    "verified_path": "/renders/1.png",
                    "reason": "scale mismatch",
                },
                {
                    "todo_id": "todo-1",
                    "status": "mismatch",
                    "failure_bucket": "scale",
                    "verified_path": "/renders/2.png",
                    "reason": "scale mismatch",
                },
            ],
            "convergence_intervention_count": 1,
        },
        verification={
            "status": "mismatch",
            "reason": "Still too large.",
            "scale_feedback": "Scale is still off.",
        },
        quality_status="mismatch",
        quality_reason="Still too large.",
        active_todo_id="todo-1",
        last_verified_path="/renders/3.png",
    )

    assert result["convergence_eval"]["status"] == "guided_retry"
    assert result["convergence_intervention_count"] == 2


def test_evaluate_convergence_detects_longer_material_oscillation():
    previous_history = [
        {
            "todo_id": "todo-1",
            "status": "mismatch",
            "failure_bucket": "scale",
            "verified_path": "/renders/1.png",
            "reason": "scale mismatch",
        },
        {
            "todo_id": "todo-1",
            "status": "mismatch",
            "failure_bucket": "material",
            "verified_path": "/renders/2.png",
            "reason": "material mismatch",
        },
        {
            "todo_id": "todo-1",
            "status": "mismatch",
            "failure_bucket": "scale",
            "verified_path": "/renders/3.png",
            "reason": "scale mismatch",
        },
        {
            "todo_id": "todo-1",
            "status": "mismatch",
            "failure_bucket": "material",
            "verified_path": "/renders/4.png",
            "reason": "material mismatch",
        },
    ]

    result = evaluate_convergence(
        state={
            "recent_verification_signatures": previous_history,
            "convergence_intervention_count": 0,
        },
        verification={
            "status": "mismatch",
            "reason": "Scale drifted again.",
            "scale_feedback": "Scale is off again.",
        },
        quality_status="mismatch",
        quality_reason="Scale drifted again.",
        active_todo_id="todo-1",
        last_verified_path="/renders/5.png",
    )

    assert result["convergence_eval"]["status"] == "guided_retry"
    assert result["convergence_eval"]["pattern"] == "oscillation_loop"
