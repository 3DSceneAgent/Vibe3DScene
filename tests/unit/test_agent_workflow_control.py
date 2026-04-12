from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from scene_agent.agent.graph import (
    _route_after_evaluator,
    _route_after_post_builder,
    _route_after_router,
    _route_after_scene_observe,
    _route_after_turn_dispatch,
    _route_after_update_memory,
    _route_after_verifier_feedback,
)
from scene_agent.agent.nodes import (
    RENDER_VISION_MESSAGE_ID,
    SCENE_OBSERVE_MESSAGE_ID,
    finalize_node,
    latest_human_message,
    planner_refresh_node,
    post_builder_node,
    scene_observe_node,
    turn_dispatch_node,
    update_memory_node,
    verifier_feedback_node,
)


def _todo(todo_id: str, description: str, status: str) -> dict:
    return {
        "id": todo_id,
        "description": description,
        "status": status,
        "created_at": "2026-01-01T00:00:00",
        "completed_at": None,
    }


def test_turn_dispatch_binary_has_calls():
    result = turn_dispatch_node(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{"name": "get_scene_info", "args": {}, "id": "tc1", "type": "tool_call"}],
                )
            ]
        }
    )
    assert result["assistant_turn_kind"] == "has_calls"
    assert result["request_agent_turns"] == 1


def test_turn_dispatch_binary_no_calls():
    result = turn_dispatch_node({"messages": [AIMessage(content="No tool needed.")], "request_agent_turns": 1})
    assert result["assistant_turn_kind"] == "no_calls"
    assert result["request_agent_turns"] == 2


def test_turn_dispatch_stops_when_agent_turn_budget_reached():
    result = turn_dispatch_node(
        {
            "messages": [AIMessage(content="One more try.")],
            "request_agent_turns": 1,
            "max_request_agent_turns": 2,
        }
    )
    assert result["request_stop_reason"] == "max_request_agent_turns_reached"
    assert result["transition_next"] == "finalize"
    assert _route_after_turn_dispatch(result) == "finalize"


def test_route_after_router_uses_plan_flag():
    assert _route_after_router({"routed_to_plan": True}) == "plan_node"
    assert _route_after_router({"routed_to_plan": False}) == "agent"


def test_route_after_turn_dispatch_uses_binary_kind():
    assert _route_after_turn_dispatch({"assistant_turn_kind": "has_calls"}) == "tools"
    assert _route_after_turn_dispatch({"assistant_turn_kind": "no_calls"}) == "evaluator"


def test_route_after_evaluator_handles_end_and_finalize():
    assert _route_after_evaluator({"transition_next": "__end__"}) == "__end__"
    assert _route_after_evaluator({"transition_next": "finalize"}) == "finalize"


def test_post_builder_node_increments_stall_count_without_tool_calls():
    result = post_builder_node(
        {
            "messages": [AIMessage(content="Trying to plan next step")],
            "request_agent_turns": 0,
            "builder_turn_count": 0,
            "builder_stall_count": 0,
        }
    )
    assert result["request_agent_turns"] == 1
    assert result["builder_turn_count"] == 1
    assert result["builder_stall_count"] == 1
    assert result["assistant_turn_kind"] == "no_calls"


def test_verifier_feedback_routes_has_calls_and_no_calls():
    with_calls = verifier_feedback_node(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{"name": "camera_observe", "args": {}, "id": "tc1", "type": "tool_call"}],
                )
            ]
        }
    )
    assert with_calls["assistant_turn_kind"] == "has_calls"
    assert _route_after_verifier_feedback(with_calls) == "tools"

    no_calls = verifier_feedback_node({"messages": [AIMessage(content='{"status":"done","reason":"ok"}')]})
    assert no_calls["assistant_turn_kind"] == "no_calls"
    assert no_calls["verification_result"]["status"] == "done"
    assert _route_after_verifier_feedback(no_calls) == "evaluator"


def test_route_after_update_memory_switches_by_topology():
    assert _route_after_update_memory({"workflow_topology": "single_agent"}) == "scene_observe"
    assert _route_after_update_memory({"workflow_topology": "dual_agent"}) == "verifier_agent"


def test_route_after_scene_observe_skips_verify_without_fresh_render_in_plan_mode():
    assert (
        _route_after_scene_observe(
            {
                "task_mode": "plan_mode",
                "last_render_path": "https://example.com/renders/current.png",
                "last_verified_path": "https://example.com/renders/current.png",
            }
        )
        == "evaluator"
    )


def test_route_after_scene_observe_keeps_verify_for_fresh_render_in_plan_mode():
    assert (
        _route_after_scene_observe(
            {
                "task_mode": "plan_mode",
                "last_render_path": "https://example.com/renders/new.png",
                "last_verified_path": "https://example.com/renders/old.png",
            }
        )
        == "verify"
    )


def test_route_after_scene_observe_preserves_direct_mode_behavior():
    assert (
        _route_after_scene_observe(
            {
                "task_mode": "direct_mode",
                "last_render_path": "https://example.com/renders/current.png",
                "last_verified_path": "https://example.com/renders/current.png",
            }
        )
        == "verify"
    )


def test_update_memory_stops_when_tool_batch_budget_reached():
    result = update_memory_node(
        {
            "messages": [
                ToolMessage(
                    content='{"scene_objects": []}',
                    name="get_scene_info",
                    tool_call_id="tc1",
                )
            ],
            "request_tool_batches": 1,
            "max_request_tool_batches": 2,
        }
    )
    assert result["request_tool_batches"] == 2
    assert result["request_stop_reason"] == "max_request_tool_batches_reached"
    assert result["transition_next"] == "finalize"
    assert _route_after_update_memory(result) == "finalize"


def test_finalize_summary_mentions_skipped_todos():
    result = finalize_node(
        {
            "task_mode": "plan_mode",
            "transition_reason": "all_todos_terminal_after_skip",
            "todos": [
                _todo("todo-1", "Create cube", "completed"),
                _todo("todo-2", "Create chair", "skipped"),
            ],
            "messages": [],
        }
    )
    content = result["messages"][0].content
    assert "skipped" in content.lower()


def test_latest_human_message_skips_internal_render_and_scene_observe_messages():
    state = {
        "messages": [
            HumanMessage(content="Create a red chair beside a wooden table."),
            HumanMessage(
                id=RENDER_VISION_MESSAGE_ID,
                content=[
                    {"type": "text", "text": "Latest render from tool call."},
                    {"type": "image_url", "image_url": {"url": "https://example.com/agent_render.png"}},
                ],
            ),
            HumanMessage(
                id=SCENE_OBSERVE_MESSAGE_ID,
                content=[
                    {"type": "text", "text": "Auto scene observation."},
                    {"type": "image_url", "image_url": {"url": "https://example.com/scene_ne.png"}},
                ],
            ),
        ]
    }
    assert latest_human_message(state) == "Create a red chair beside a wooden table."


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
            "enabled_tool_names": ["camera_observe", "render_from_camera"],
            "tool_round_count": 2,
        }
    )

    assert result.get("last_render_source") == "scene_observe"
    assert result.get("last_render_path") == "https://example.com/renders/scene_ne.png"
    assert calls == [("probe", {"foo": "bar"}, "thread-scene-observe")]


def test_scene_observe_node_clears_verification_result_when_no_scene_mutation():
    result = scene_observe_node(
        {
            "thread_id": "thread-no-mutation",
            "last_tool_batch_names": ["render_from_camera"],
            "verification_result": {"status": "working", "reason": "stale"},
            "messages": [],
        }
    )

    assert result == {"verification_result": None}


def test_planner_refresh_node_generates_replan_todos_from_verification_result():
    result = planner_refresh_node(
        {
            "verification_result": {
                "status": "working",
                "reason": "layout mismatch",
                "edit_suggestions": ["Move chair closer to table"],
            },
            "plan_replan_count": 0,
            "max_plan_replans": 2,
            "todos": [_todo("todo-open-1", "Place chair", "in_progress")],
            "active_todo_id": "todo-open-1",
        }
    )
    assert result["plan_replan_count"] == 1
    assert len(result["todos"]) >= 1
