from langchain_core.messages import ToolMessage

from scene_agent.agent.nodes import (
    budget_evaluator_node,
    progress_evaluator_node,
    quality_evaluator_node,
    transition_resolver_node,
)


def _todo(todo_id: str, description: str, status: str) -> dict:
    return {
        "id": todo_id,
        "description": description,
        "status": status,
        "created_at": "2026-01-01T00:00:00",
        "completed_at": None,
    }


def test_quality_evaluator_maps_match_status():
    result = quality_evaluator_node(
        {
            "messages": [
                ToolMessage(
                    name="verification",
                    content={"status": "match", "reason": "Looks good."},
                    tool_call_id="verification_match",
                )
            ],
            "verification_mismatch_streak": 2,
        }
    )
    assert result["quality_eval"]["status"] == "match"
    assert result["verification_mismatch_streak"] == 0


def test_quality_evaluator_maps_mismatch_status_and_increments_streak():
    result = quality_evaluator_node(
        {
            "messages": [
                ToolMessage(
                    name="verification",
                    content={"status": "mismatch", "reason": "Object missing."},
                    tool_call_id="verification_mismatch",
                )
            ],
            "verification_mismatch_streak": 1,
        }
    )
    assert result["quality_eval"]["status"] == "mismatch"
    assert result["verification_mismatch_streak"] == 2


def test_progress_evaluator_returns_continue_with_open_todos():
    result = progress_evaluator_node(
        {
            "task_mode": "plan_mode",
            "quality_eval": {"status": "mismatch", "reason": "Need fixes."},
            "todos": [_todo("todo-1", "Move chair", "in_progress")],
            "verification_mismatch_streak": 2,
        }
    )
    assert result["progress_eval"]["status"] == "continue"
    assert result["progress_eval"]["should_replan"] is True


def test_progress_evaluator_returns_blocked_for_catastrophic_without_open_todos():
    result = progress_evaluator_node(
        {
            "task_mode": "plan_mode",
            "quality_eval": {"status": "catastrophic", "reason": "Scene exploded."},
            "todos": [],
        }
    )
    assert result["progress_eval"]["status"] == "blocked"


def test_budget_evaluator_detects_agent_turn_exhaustion():
    result = budget_evaluator_node(
        {
            "request_agent_turns": 8,
            "max_request_agent_turns": 8,
            "request_tool_batches": 1,
            "max_request_tool_batches": 6,
        }
    )
    assert result["budget_eval"]["budget_ok"] is False
    assert result["budget_eval"]["stop_reason"] == "agent_turn_budget_exhausted"


def test_transition_resolver_routes_to_agent_for_single_continue():
    result = transition_resolver_node(
        {
            "task_mode": "single_action_mode",
            "workflow_topology": "single_agent",
            "budget_eval": {"budget_ok": True, "stop_reason": None},
            "quality_eval": {"status": "mismatch", "reason": "Need one more edit."},
            "progress_eval": {"status": "continue", "should_replan": False},
        }
    )
    assert result["transition_next"] == "agent"


def test_transition_resolver_routes_to_builder_for_dual_catastrophic():
    result = transition_resolver_node(
        {
            "task_mode": "plan_mode",
            "workflow_topology": "dual_agent",
            "budget_eval": {"budget_ok": True, "stop_reason": None},
            "quality_eval": {"status": "catastrophic", "reason": "Catastrophic"},
            "progress_eval": {"status": "continue", "should_replan": True},
        }
    )
    assert result["transition_next"] == "builder_agent"


def test_transition_resolver_routes_to_planner_refresh_for_dual_replan_signal():
    result = transition_resolver_node(
        {
            "task_mode": "plan_mode",
            "workflow_topology": "dual_agent",
            "budget_eval": {"budget_ok": True, "stop_reason": None},
            "quality_eval": {"status": "mismatch", "reason": "Needs replan"},
            "progress_eval": {"status": "continue", "should_replan": True},
            "plan_replan_count": 0,
            "max_plan_replans": 2,
        }
    )
    assert result["transition_next"] == "planner_refresh"
