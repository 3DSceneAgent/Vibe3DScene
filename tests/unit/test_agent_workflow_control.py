from langchain_core.messages import AIMessage

from scene_agent.agent.graph import _route_after_todo_check
from scene_agent.agent.nodes import (
    checkpoint_gate_node,
    post_agent_node,
    todo_check_node,
)


def _todo(
    todo_id: str,
    description: str,
    status: str,
    *,
    completed_at: str | None = None,
) -> dict:
    return {
        "id": todo_id,
        "description": description,
        "status": status,
        "created_at": "2026-01-01T00:00:00",
        "completed_at": completed_at,
    }


def test_checkpoint_gate_skips_without_todos_in_loop():
    result = checkpoint_gate_node({"tool_round_count": 3}, stage="loop")
    gate = result["todo_check_gate"]
    assert gate["should_run"] is False
    assert gate["reason"] == "no_todos"


def test_checkpoint_gate_runs_on_interval_when_todos_exist():
    result = checkpoint_gate_node(
        {
            "todos": [_todo("todo-1", "Import table", "in_progress")],
            "tool_round_count": 4,
            "last_todo_check_round": 1,
        },
        stage="loop",
    )
    gate = result["todo_check_gate"]
    assert gate["should_run"] is True
    assert gate["reason"] == "interval_reached"


def test_checkpoint_gate_runs_before_finalize_when_todos_exist():
    result = checkpoint_gate_node(
        {
            "todos": [_todo("todo-1", "Import table", "in_progress")],
            "tool_round_count": 1,
            "last_todo_check_round": 1,
        },
        stage="finalize",
    )
    gate = result["todo_check_gate"]
    assert gate["should_run"] is True
    assert gate["reason"] == "pre_finalize_guard"


def test_todo_check_not_applicable_when_no_todos():
    result = todo_check_node({"tool_round_count": 2, "todo_check_gate": {"stage": "loop"}})
    assert result["todo_check"]["status"] == "not_applicable"
    assert result["todo_check"]["reason"] == "no_todos"


def test_todo_check_completed_when_no_pending_or_in_progress():
    result = todo_check_node(
        {
            "todos": [
                _todo("todo-1", "Import table", "completed", completed_at="2026-01-01T00:10:00"),
                _todo("todo-2", "Add cup", "failed"),
            ],
            "tool_round_count": 5,
            "todo_check_gate": {"stage": "loop"},
        }
    )
    assert result["todo_check"]["status"] == "completed"
    assert result["todo_check"]["reason"] == "all_todos_terminal"


def test_todo_check_blocks_after_stagnation_limit():
    result = todo_check_node(
        {
            "todos": [_todo("todo-1", "Import table", "pending")],
            "tool_round_count": 6,
            "todo_check_gate": {"stage": "loop"},
            "last_todo_snapshot": {"import table": "pending"},
            "stagnation_count": 1,
        }
    )
    assert result["todo_check"]["status"] == "blocked"
    assert result["todo_check"]["reason"] == "todo_progress_stagnant"
    assert result["stagnation_count"] == 2


def test_post_agent_reuses_todo_id_by_description():
    state = {
        "messages": [
            AIMessage(
                content=(
                    "<todos>\n"
                    "- [completed] Import table\n"
                    "</todos>\n"
                    '<agent_decision>{"should_call_tools": false}</agent_decision>'
                )
            )
        ],
        "todos": [_todo("todo-existing", "Import table", "in_progress")],
    }
    result = post_agent_node(state)
    assert result["todos"][0]["id"] == "todo-existing"
    assert result["todos"][0]["status"] == "completed"


def test_route_after_todo_check_continues_when_finalize_stage_not_terminal():
    next_node = _route_after_todo_check(
        {
            "todo_check_gate": {"stage": "finalize"},
            "todo_check": {"status": "continue"},
            "last_render_path": None,
            "agent_decision": {"should_verify": False},
        }
    )
    assert next_node == "agent"


def test_route_after_todo_check_finalizes_when_finalize_stage_terminal():
    next_node = _route_after_todo_check(
        {
            "todo_check_gate": {"stage": "finalize"},
            "todo_check": {"status": "completed"},
            "last_render_path": None,
            "agent_decision": {"should_verify": False},
        }
    )
    assert next_node == "finalize"
