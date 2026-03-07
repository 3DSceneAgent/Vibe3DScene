"""Runtime constants for execution/finalize-guard behavior.

This module keeps constants that are tied to scene mutation, guard cadence,
and fixed internal message IDs used in graph state updates.
"""

from __future__ import annotations

# Finalize-guard cadence and recovery thresholds.
TODO_CHECK_INTERVAL_ROUNDS = 3
TODO_STAGNATION_LIMIT = 2
TODO_BLOCKED_RECOVERY_ATTEMPTS = 2

# Tools that can mutate scene state and should trigger scene-level observation.
SCENE_MUTATING_TOOLS: frozenset[str] = frozenset(
    {
        "clear_scene",
        "execute_blender_code",
        "delete_objects",
        "import_glb_model",
        "import_blend_contents",
        "download_polyhaven_asset",
        "set_texture",
        "generate_trellis2_model",
        "import_retrieved_asset",
        "download_sketchfab_model",
        "generate_infinigen_assets",
        "import_generated_asset",
        "generate_hunyuan3d_model",
        "generate_hyper3d_model_via_text",
        "generate_hyper3d_model_via_images",
        "undo_last_snapshot",
    }
)

# Fixed IDs for internal visual-context messages.
# `add_messages` replaces by ID, preventing unbounded context growth.
RENDER_VISION_MESSAGE_ID = "render_vision_current"
SCENE_OBSERVE_MESSAGE_ID = "scene_observe_current"
TODO_BLOCKED_RECOVERY_MESSAGE_ID = "todo_blocked_recovery_current"
TODO_BLOCKED_RECOVERY_ACTION_MESSAGE_ID = "todo_blocked_recovery_action_current"
