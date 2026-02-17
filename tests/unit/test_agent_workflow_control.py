from langchain_core.messages import AIMessage, HumanMessage

from scene_agent.agent.graph import _route_after_post_agent, _route_after_todo_check
from scene_agent.agent.nodes import (
    _latest_human_message,
    checkpoint_gate_node,
    post_agent_node,
    scene_observe_node,
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


def test_route_after_post_agent_retries_once_when_tools_expected_but_missing():
    next_node = _route_after_post_agent(
        {
            "messages": [AIMessage(content="Continuing with tool calls.")],
            "agent_decision": {"should_call_tools": True},
            "iteration_count": 1,
        }
    )
    assert next_node == "agent"


def test_route_after_post_agent_finalizes_after_retry_budget_exhausted():
    next_node = _route_after_post_agent(
        {
            "messages": [AIMessage(content="Continuing with tool calls.")],
            "agent_decision": {"should_call_tools": True},
            "iteration_count": 2,
        }
    )
    assert next_node == "checkpoint_finalize"


def test_scene_observe_node_routes_commands_via_api_sender(monkeypatch):
    calls: list[tuple[str, dict | None, str | None]] = []

    def fake_send_blender_command_sync(
        command_type: str,
        params: dict | None = None,
        thread_id: str | None = None,
    ) -> dict:
        calls.append((command_type, params, thread_id))
        return {"success": True}

    def fake_update_scene_cameras(
        *,
        thread_id: str = "unknown",
        send_blender_command=None,
    ) -> dict:
        assert thread_id == "thread-scene-observe"
        assert callable(send_blender_command)
        probe = send_blender_command("probe", {"foo": "bar"})
        assert probe == {"success": True}
        return {
            "success": True,
            "scene_bbox": {"center": [0.0, 0.0, 0.0], "dimensions": [1.0, 1.0, 1.0]},
            "cameras": [
                {
                    "camera_name": "SceneCamera_NE",
                    "location": [1.0, 1.0, 1.0],
                    "focal_mm": 50.0,
                    "azimuth": 45.0,
                    "elevation": 30.0,
                    "image_url": "https://example.com/renders/scene_ne.png",
                }
            ],
            "image_urls": ["https://example.com/renders/scene_ne.png"],
        }

    monkeypatch.setattr(
        "scene_agent.interfaces.api.send_blender_command_sync",
        fake_send_blender_command_sync,
    )
    monkeypatch.setattr(
        "mcp_server.tools.multimodal.camera_tools.update_scene_cameras",
        fake_update_scene_cameras,
    )

    result = scene_observe_node(
        {
            "last_tool_batch_names": ["execute_blender_code"],
            "thread_id": "thread-scene-observe",
            "tool_round_count": 2,
        }
    )

    assert result.get("last_render_source") == "scene_observe"
    assert result.get("last_render_path") == "https://example.com/renders/scene_ne.png"
    assert calls == [("probe", {"foo": "bar"}, "thread-scene-observe")]


def test_latest_human_message_skips_internal_render_and_scene_observe_messages():
    state = {
        "messages": [
            HumanMessage(content="Create a red chair beside a wooden table."),
            HumanMessage(
                content=[
                    {"type": "text", "text": "Latest render from tool call."},
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://example.com/agent_render.png"},
                    },
                ],
                additional_kwargs={"internal_source": "tool_render_observe"},
            ),
            # Legacy untagged message should still be ignored by prefix match.
            HumanMessage(
                content=[
                    {
                        "type": "text",
                        "text": "Auto scene observation — 4-view render after scene mutation.",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://example.com/scene_ne.png"},
                    },
                ]
            ),
        ]
    }
    assert _latest_human_message(state) == "Create a red chair beside a wooden table."
