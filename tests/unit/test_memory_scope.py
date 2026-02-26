from scene_agent.agent.memory_scope import merge_role_private_memory, resolve_memory_profile


def test_merge_role_private_memory_creates_and_updates_role_bucket():
    merged = merge_role_private_memory(
        None,
        role="builder",
        patch={"last_action_summary": "move chair"},
    )
    assert merged["builder"]["last_action_summary"] == "move chair"

    merged_again = merge_role_private_memory(
        merged,
        role="builder",
        patch={"replan_count": 1},
    )
    assert merged_again["builder"]["last_action_summary"] == "move chair"
    assert merged_again["builder"]["replan_count"] == 1


def test_resolve_memory_profile_defaults_to_thread_shared_only():
    assert resolve_memory_profile(None) == "thread_shared_only"
    assert resolve_memory_profile("shared_plus_role_private") == "shared_plus_role_private"
