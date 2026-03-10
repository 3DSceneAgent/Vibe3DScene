from scene_agent.agent.tool_policy import (
    VERIFIER_CAMERA_TOOLS,
    apply_requested_tool_filter,
    resolve_effective_tool_names,
)


def test_apply_requested_tool_filter_respects_subset_order():
    available = ["get_scene_info", "delete_objects", "render_from_camera"]
    requested = ["render_from_camera", "get_scene_info"]
    assert apply_requested_tool_filter(available, requested) == [
        "get_scene_info",
        "render_from_camera",
    ]


def test_resolve_effective_tool_names_direct_mode_general_keeps_mutation_tools():
    names, reason = resolve_effective_tool_names(
        mode="direct_mode",
        role="general",
        available_tool_names=["get_scene_info", "delete_objects", "render_from_camera"],
        requested_tool_names=None,
        request_tool_batches=0,
        max_request_tool_batches=0,
    )
    assert reason is None
    assert names == ["get_scene_info", "delete_objects", "render_from_camera"]


def test_resolve_effective_tool_names_verifier_role_uses_camera_profile():
    names, reason = resolve_effective_tool_names(
        mode="plan_mode",
        role="verifier",
        available_tool_names=["get_scene_info", "delete_objects", "render_from_camera", "camera_set_pose"],
        requested_tool_names=None,
        request_tool_batches=0,
        max_request_tool_batches=3,
    )
    assert reason == "verifier_role_camera_tools"
    assert names == ["get_scene_info", "render_from_camera", "camera_set_pose"]
    assert set(names).issubset(VERIFIER_CAMERA_TOOLS)


def test_resolve_effective_tool_names_builder_excludes_camera_tools():
    names, reason = resolve_effective_tool_names(
        mode="plan_mode",
        role="builder",
        available_tool_names=["get_scene_info", "render_from_camera", "camera_set_pose", "delete_objects"],
        requested_tool_names=None,
        request_tool_batches=0,
        max_request_tool_batches=3,
    )
    assert reason is None
    assert names == ["get_scene_info", "delete_objects"]
