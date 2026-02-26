from scene_agent.agent.workflow_profiles import (
    normalize_memory_profile_request,
    normalize_workflow_topology_request,
    resolve_memory_profile,
    resolve_workflow_topology,
)


def test_normalize_workflow_topology_request_variants():
    assert normalize_workflow_topology_request("dual") == "dual_agent"
    assert normalize_workflow_topology_request("single-agent") == "single_agent"
    assert normalize_workflow_topology_request("unknown") == "auto"


def test_resolve_workflow_topology_for_non_plan_mode_forces_single():
    assert (
        resolve_workflow_topology(
            task_mode="conversation_mode",
            requested_topology="dual_agent",
        )
        == "single_agent"
    )


def test_resolve_workflow_topology_for_plan_mode_respects_explicit_request():
    assert (
        resolve_workflow_topology(
            task_mode="plan_mode",
            requested_topology="dual_agent",
        )
        == "dual_agent"
    )
    assert (
        resolve_workflow_topology(
            task_mode="plan_mode",
            requested_topology="single_agent",
        )
        == "single_agent"
    )


def test_memory_profile_resolution():
    assert normalize_memory_profile_request("shared-plus-role-private") == "shared_plus_role_private"
    assert resolve_memory_profile("shared_plus_role_private") == "shared_plus_role_private"
    assert resolve_memory_profile("auto") == "thread_shared_only"
