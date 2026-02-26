from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from scene_agent.agent.graph import (
    _route_after_blocked_recovery_action,
    _route_after_loop_checkpoint,
    _route_after_post_agent,
    _route_after_post_builder,
    _route_after_post_verifier,
    _route_after_verify,
    _route_after_todo_check,
)
from scene_agent.agent.nodes import (
    _RENDER_VISION_MESSAGE_ID,
    _SCENE_OBSERVE_MESSAGE_ID,
    _latest_human_message,
    blocked_recovery_action_node,
    blocked_recovery_node,
    checkpoint_gate_node,
    finalize_node,
    planner_refresh_node,
    post_agent_node,
    post_builder_node,
    route_mode_node,
    scene_observe_node,
    todo_check_node,
    transition_resolver_node,
    verifier_agent_node,
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


def test_route_mode_node_classifies_conversation_mode_for_simple_qa():
    result = route_mode_node({"messages": [HumanMessage(content="What is global illumination?")]})
    assert result["task_mode"] == "conversation_mode"
    assert result["max_request_tool_batches"] == 0


def test_route_mode_node_classifies_single_action_mode_for_single_edit():
    result = route_mode_node({"messages": [HumanMessage(content="Add a wooden chair to the scene.")]})
    assert result["task_mode"] == "single_action_mode"
    assert result["max_request_tool_batches"] == 1


def test_route_mode_node_forces_plan_mode_when_unfinished_todos_exist():
    result = route_mode_node(
        {
            "messages": [HumanMessage(content="continue")],
            "todos": [_todo("todo-1", "Arrange room layout", "in_progress")],
        }
    )
    assert result["task_mode"] == "plan_mode"
    assert result["task_intent"] == "continue_existing_plan"


def test_route_mode_node_sets_dual_topology_for_plan_mode_when_requested():
    result = route_mode_node(
        {
            "messages": [HumanMessage(content="Create a chair and then add a lamp.")],
            "workflow_topology_request": "dual_agent",
        }
    )
    assert result["task_mode"] == "plan_mode"
    assert result["workflow_topology"] == "dual_agent"
    assert result["active_role"] == "builder"


def test_route_mode_node_downgrades_dual_request_for_conversation_mode():
    result = route_mode_node(
        {
            "messages": [HumanMessage(content="What is global illumination?")],
            "workflow_topology_request": "dual_agent",
        }
    )
    assert result["task_mode"] == "conversation_mode"
    assert result["workflow_topology"] == "single_agent"
    assert result["active_role"] == "general"


def test_post_agent_reuses_todo_id_by_description():
    state = {
        "messages": [
            AIMessage(
                content=(
                    "<todos>\n"
                    "- [completed] Import table\n"
                    "</todos>\n"
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
        }
    )
    assert next_node == "agent"


def test_route_after_todo_check_routes_blocked_to_agent_in_single_mode():
    next_node = _route_after_todo_check(
        {
            "todo_check_gate": {"stage": "finalize"},
            "todo_check": {"status": "blocked", "stagnation_count": 2},
            "last_render_path": None,
        }
    )
    assert next_node == "agent"


def test_route_after_todo_check_keeps_blocked_on_agent_path_even_with_high_stagnation():
    next_node = _route_after_todo_check(
        {
            "todo_check_gate": {"stage": "finalize"},
            "todo_check": {"status": "blocked", "stagnation_count": 4},
            "last_render_path": None,
        }
    )
    assert next_node == "agent"


def test_route_after_todo_check_finalizes_when_finalize_stage_terminal():
    next_node = _route_after_todo_check(
        {
            "todo_check_gate": {"stage": "finalize"},
            "todo_check": {"status": "completed"},
            "last_render_path": None,
        }
    )
    assert next_node == "finalize"


def test_blocked_recovery_node_prioritizes_undo_when_available():
    result = blocked_recovery_node(
        {
            "todo_check": {"status": "blocked", "stagnation_count": 2},
            "enabled_tool_names": ["get_scene_info", "observe_scene_global", "undo_last_snapshot"],
        }
    )
    message = result["messages"][0]
    content = message.content
    assert "Recovery mode" in content
    assert "undo_last_snapshot" in content


def test_blocked_recovery_node_second_attempt_forces_reset_even_with_undo_available():
    result = blocked_recovery_node(
        {
            "todo_check": {"status": "blocked", "stagnation_count": 3},
            "enabled_tool_names": ["get_scene_info", "observe_scene_global", "undo_last_snapshot"],
        }
    )
    message = result["messages"][0]
    content = message.content
    assert "Skip undo and run full reset now." in content
    assert "delete_objects(object_names=[...], mode=\"cascade\", strict=False, ignore_missing=True)" in content


def test_blocked_recovery_node_prefers_clear_scene_when_available():
    result = blocked_recovery_node(
        {
            "todo_check": {"status": "blocked", "stagnation_count": 2},
            "enabled_tool_names": ["clear_scene", "get_scene_info", "observe_scene_global"],
        }
    )
    message = result["messages"][0]
    content = message.content
    assert "clear_scene()" in content
    assert "delete_objects(object_names=[...]" not in content


def test_blocked_recovery_node_uses_clear_and_rebuild_when_undo_unavailable():
    result = blocked_recovery_node(
        {
            "todo_check": {"status": "blocked", "stagnation_count": 2},
            "enabled_tool_names": ["get_scene_info", "delete_objects"],
        }
    )
    message = result["messages"][0]
    content = message.content
    assert "undo_last_snapshot` is unavailable" in content
    assert "delete_objects" in content


def test_blocked_recovery_action_node_attempt_one_prefers_undo_and_observe():
    result = blocked_recovery_action_node(
        {
            "todo_check": {"status": "blocked", "stagnation_count": 2},
            "enabled_tool_names": ["undo_last_snapshot", "get_scene_info", "observe_scene_global"],
        }
    )
    message = result["messages"][0]
    tool_names = [call["name"] for call in message.tool_calls]
    assert tool_names == ["undo_last_snapshot", "get_scene_info", "observe_scene_global"]


def test_blocked_recovery_action_node_attempt_two_prefers_clear_scene():
    result = blocked_recovery_action_node(
        {
            "todo_check": {"status": "blocked", "stagnation_count": 3},
            "enabled_tool_names": ["clear_scene", "get_scene_info", "observe_scene_global"],
        }
    )
    message = result["messages"][0]
    tool_names = [call["name"] for call in message.tool_calls]
    assert tool_names == ["clear_scene", "get_scene_info", "observe_scene_global"]


def test_route_after_blocked_recovery_action_routes_to_tools_on_forced_calls():
    next_node = _route_after_blocked_recovery_action(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "clear_scene", "args": {}, "id": "tc-reset", "type": "tool_call"},
                    ],
                )
            ]
        }
    )
    assert next_node == "tools"


def test_route_after_verify_routes_to_checkpoint_when_no_forced_recovery():
    next_node = _route_after_verify({"verify_forced_recovery": False})
    assert next_node == "checkpoint_loop"


def test_route_after_verify_routes_to_verifier_in_dual_plan_mode():
    next_node = _route_after_verify(
        {
            "verify_forced_recovery": False,
            "task_mode": "plan_mode",
            "workflow_topology": "dual_agent",
        }
    )
    assert next_node == "verifier_camera_agent"


def test_route_after_post_builder_routes_to_verifier_when_no_tool_calls():
    next_node = _route_after_post_builder(
        {
            "messages": [AIMessage(content="Verifier should inspect this result.")],
            "request_agent_turns": 1,
            "max_request_agent_turns": 8,
        }
    )
    assert next_node == "verifier_camera_agent"


def test_route_after_post_verifier_routes_to_tools_when_tool_calls_present():
    next_node = _route_after_post_verifier(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "camera_set_pose",
                            "args": {"location": [0, 0, 2], "rotation_euler": [1, 0, 0]},
                            "id": "tc-camera-pose",
                            "type": "tool_call",
                        }
                    ],
                )
            ],
            "request_agent_turns": 1,
            "max_request_agent_turns": 8,
        }
    )
    assert next_node == "tools"


def test_route_after_post_verifier_routes_to_feedback_without_tool_calls():
    next_node = _route_after_post_verifier(
        {
            "messages": [AIMessage(content="Need builder to move the lamp 10cm left.")],
            "request_agent_turns": 1,
            "max_request_agent_turns": 8,
        }
    )
    assert next_node == "verifier_feedback"


def test_route_after_post_verifier_routes_to_checkpoint_on_turn_budget_exhaustion():
    next_node = _route_after_post_verifier(
        {
            "messages": [AIMessage(content="No more tool calls.")],
            "request_agent_turns": 8,
            "max_request_agent_turns": 8,
        }
    )
    assert next_node == "checkpoint_finalize"


def test_route_after_post_agent_retries_once_when_tools_expected_but_missing():
    next_node = _route_after_post_agent(
        {
            "messages": [AIMessage(content="Continuing with tool calls.")],
            "task_mode": "plan_mode",
            "request_agent_turns": 1,
            "max_request_agent_turns": 8,
        }
    )
    assert next_node == "agent"


def test_route_after_post_agent_finalizes_after_retry_budget_exhausted():
    next_node = _route_after_post_agent(
        {
            "messages": [AIMessage(content="Continuing with tool calls.")],
            "task_mode": "plan_mode",
            "request_agent_turns": 2,
            "max_request_agent_turns": 8,
        }
    )
    assert next_node == "checkpoint_finalize"


def test_route_after_loop_checkpoint_routes_to_builder_for_dual_plan_mode():
    next_node = _route_after_loop_checkpoint(
        {
            "task_mode": "plan_mode",
            "workflow_topology": "dual_agent",
            "todo_check_gate": {"should_run": False},
        }
    )
    assert next_node == "builder_agent"


def test_route_after_todo_check_returns_builder_for_dual_plan_mode():
    next_node = _route_after_todo_check(
        {
            "task_mode": "plan_mode",
            "workflow_topology": "dual_agent",
            "todo_check_gate": {"stage": "loop"},
            "todo_check": {"status": "continue"},
        }
    )
    assert next_node == "builder_agent"


def test_route_after_todo_check_returns_builder_for_dual_plan_blocked_finalize_stage():
    next_node = _route_after_todo_check(
        {
            "task_mode": "plan_mode",
            "workflow_topology": "dual_agent",
            "todo_check_gate": {"stage": "finalize"},
            "todo_check": {"status": "blocked", "stagnation_count": 3},
        }
    )
    assert next_node == "builder_agent"


def test_route_after_blocked_recovery_action_returns_builder_for_dual_plan_without_calls():
    next_node = _route_after_blocked_recovery_action(
        {
            "task_mode": "plan_mode",
            "workflow_topology": "dual_agent",
            "messages": [AIMessage(content="no tool call")],
        }
    )
    assert next_node == "builder_agent"


def test_finalize_node_emits_summary_message():
    result = finalize_node(
        {
            "task_mode": "plan_mode",
            "request_agent_turns": 3,
            "request_tool_batches": 2,
            "todo_check": {
                "status": "completed",
                "reason": "all_todos_terminal",
                "pending_count": 0,
                "in_progress_count": 0,
                "completed_count": 2,
                "failed_count": 0,
            },
            "messages": [],
        }
    )
    assert result["workflow"]["workflow_status"] == "finished"
    assert result["workflow"]["finish_reason"] == "todos_completed"
    assert "messages" in result
    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], AIMessage)
    assert "Scene workflow finished" in result["messages"][0].content


def test_finalize_summary_uses_todo_check_counts_for_consistency():
    result = finalize_node(
        {
            "task_mode": "plan_mode",
            # Historical todo revisions can contain duplicates by description.
            "todos": [
                _todo("todo-1", "Create cube", "pending"),
                _todo("todo-1b", "Create cube", "completed", completed_at="2026-01-01T00:10:00"),
            ],
            "todo_check": {
                "status": "completed",
                "reason": "all_todos_terminal",
                "pending_count": 0,
                "in_progress_count": 0,
                "completed_count": 1,
                "failed_count": 0,
            },
            "messages": [],
        }
    )
    content = result["messages"][0].content
    assert "Total todos: 1." in content
    assert "Completed: 1, in progress: 0, pending: 0, failed: 0." in content


def test_finalize_node_prefers_model_generated_summary_when_available():
    class _StubFinalizer:
        def __init__(self):
            self.configs: list[dict] = []

        def with_config(self, **kwargs):
            self.configs.append(kwargs)
            return self

        def invoke(self, _messages):
            return AIMessage(content="Result\nModel summary output.")

    model = _StubFinalizer()
    result = finalize_node(
        {
            "task_mode": "plan_mode",
            "todo_check": {
                "status": "blocked",
                "reason": "todo_progress_stagnant",
                "pending_count": 1,
                "in_progress_count": 0,
                "completed_count": 0,
                "failed_count": 0,
            },
            "messages": [HumanMessage(content="Build a dungeon.")],
        },
        finalizer_model=model,
    )
    assert result["messages"][0].content == "Result\nModel summary output."
    assert any(
        cfg.get("tags") == ["nostream"] and cfg.get("run_name") == "finalize_summary_internal"
        for cfg in model.configs
    )


def test_finalize_fallback_does_not_dump_raw_verification_dict_string():
    verification_raw = (
        "{'status': 'mismatch', 'object_feedback': 'Pot of gold is missing.', "
        "'layout_feedback': 'Dragon is present but scene is incomplete.', 'reason': 'Key asset missing.'}"
    )
    result = finalize_node(
        {
            "task_mode": "plan_mode",
            "todo_check": {
                "status": "blocked",
                "reason": "todo_progress_stagnant",
                "pending_count": 2,
                "in_progress_count": 1,
                "completed_count": 0,
                "failed_count": 0,
            },
            "messages": [
                HumanMessage(content="Build a dungeon with a dragon and pot of gold."),
                ToolMessage(name="verification", content=verification_raw, tool_call_id="verification_test"),
            ],
        },
        finalizer_model=None,
    )
    content = result["messages"][0].content
    assert "Verification note: Key asset missing." in content
    assert "Verification note: {'status': 'mismatch'" not in content


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


def test_latest_human_message_skips_internal_render_and_scene_observe_messages():
    state = {
        "messages": [
            HumanMessage(content="Create a red chair beside a wooden table."),
            HumanMessage(
                id=_RENDER_VISION_MESSAGE_ID,
                content=[
                    {"type": "text", "text": "Latest render from tool call."},
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://example.com/agent_render.png"},
                    },
                ],
            ),
            HumanMessage(
                id=_SCENE_OBSERVE_MESSAGE_ID,
                content=[
                    {
                        "type": "text",
                        "text": "Auto scene observation — 4-view render after scene mutation.",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://example.com/scene_ne.png"},
                    },
                ],
            ),
        ]
    }
    assert _latest_human_message(state) == "Create a red chair beside a wooden table."


def test_post_builder_node_increments_stall_count_without_tool_calls():
    result = post_builder_node(
        {
            "messages": [AIMessage(content="Trying to plan next step")],
            "request_agent_turns": 0,
            "builder_turn_count": 0,
            "builder_stall_count": 0,
            "max_request_agent_turns": 8,
        }
    )
    assert result["request_agent_turns"] == 1
    assert result["builder_turn_count"] == 1
    assert result["builder_stall_count"] == 1


def test_verifier_agent_node_marks_ready_to_finalize_on_match_without_open_todos():
    result = verifier_agent_node(
        {
            "messages": [
                ToolMessage(
                    name="verification",
                    content={"status": "match", "reason": "Looks good."},
                    tool_call_id="verification_match",
                )
            ],
            "todos": [_todo("todo-1", "Add lamp", "completed", completed_at="2026-01-01T00:10:00")],
        }
    )
    feedback = result["verifier_feedback"]
    assert feedback["status"] == "pass"
    assert feedback["ready_to_finalize"] is True
    assert feedback["should_replan"] is False


def test_transition_resolver_node_routes_to_planner_refresh_on_replan_signal():
    result = transition_resolver_node(
        {
            "verifier_feedback": {
                "status": "needs_fix",
                "should_replan": True,
                "ready_to_finalize": False,
            },
            "plan_replan_count": 0,
            "max_plan_replans": 2,
        }
    )
    assert result["transition_next"] == "planner_refresh"


def test_planner_refresh_node_adds_replan_todo():
    result = planner_refresh_node(
        {
            "verifier_feedback": {
                "reason": "layout mismatch",
                "fix_instructions": ["Move chair closer to table"],
            },
            "plan_replan_count": 0,
            "max_plan_replans": 2,
        }
    )
    assert result["plan_replan_count"] == 1
    assert result["builder_stall_count"] == 0
    assert len(result["todos"]) >= 1
