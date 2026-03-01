from langchain_core.messages import ToolMessage

from scene_agent.agent.nodes import quality_evaluator_node, transition_resolver_node


def _verification_message(payload: dict, tool_call_id: str) -> ToolMessage:
    return ToolMessage(name="verification", content=payload, tool_call_id=tool_call_id)


def test_quality_evaluator_emits_guided_retry_on_repeat_loop():
    base_state = {
        "active_todo_id": "todo-1",
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
        "messages": [
            _verification_message(
                {
                    "status": "mismatch",
                    "reason": "Scale is still off.",
                    "scale_feedback": "The chair is too large.",
                },
                "verification_repeat",
            )
        ],
        "last_verified_path": "/renders/3.png",
    }

    result = quality_evaluator_node(base_state)
    assert result["convergence_eval"]["status"] == "guided_retry"
    assert result["convergence_eval"]["pattern"] == "repeat_loop"
    assert result["convergence_intervention_count"] == 1
    assert "messages" in result


def test_transition_resolver_stops_on_convergence_hard_stop():
    result = transition_resolver_node(
        {
            "task_mode": "plan_mode",
            "workflow_topology": "single_agent",
            "budget_eval": {"budget_ok": True, "stop_reason": None},
            "progress_eval": {"status": "blocked", "should_replan": False},
            "convergence_eval": {"status": "hard_stop", "reason": "repeated_same_failure_signature_after_guidance"},
            "quality_eval": {"status": "mismatch", "reason": "Still wrong"},
        }
    )

    assert result["transition_next"] == "checkpoint_finalize"
    assert result["transition_reason"] == "repeated_same_failure_signature_after_guidance"
