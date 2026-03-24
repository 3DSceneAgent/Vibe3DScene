import logging

import pytest

from mcp_server import tool_registry


class FakeMCP:
    def __init__(self) -> None:
        self.enabled: list[str] = []

    def add_tool(self, func) -> None:
        self.enabled.append(func.__name__)


def _reset_registry_state(monkeypatch) -> None:
    monkeypatch.setattr(tool_registry, "_tools_registered", False)
    monkeypatch.setattr(tool_registry, "_enabled_tool_names", [])
    tool_registry.runtime.reset_sketchfab_api_probe_cache()


def _configure_runtime(
    monkeypatch,
    *,
    mode: str = "headless",
    retrieval_provider: str = "objaverse",
    hunyuan: bool = False,
    polyhaven: bool = True,
    rodin: bool = False,
    rodin_key: str = "",
    trellis2: bool = False,
    retrieval: bool = False,
    infinigen: bool = False,
    sam_reconstruct: bool = False,
    sam_reconstruct_service: bool = True,
    sketchfab: bool = False,
    sketchfab_key: str = "",
    sketchfab_reachable: bool = True,
    scenesmith_hssd: bool = True,
    scenesmith_ambientcg: bool = False,
    scenesmith_hssd_service: bool = True,
    scenesmith_ambientcg_service: bool = True,
) -> None:
    asset_retrieval_backend = retrieval_provider if retrieval else "disabled"
    switch_values = {
        "ENABLE_HUNYUAN": hunyuan,
        "ENABLE_POLYHAVEN": polyhaven,
        "ENABLE_RODIN": rodin,
        "ENABLE_TRELLIS2": trellis2,
        "ENABLE_INFINIGEN": infinigen,
        "ENABLE_SAM_RECONSTRUCT": sam_reconstruct,
        "ENABLE_SKETCHFAB": sketchfab,
        "ENABLE_AMBIENTCG": scenesmith_ambientcg,
    }
    monkeypatch.setenv("BLENDER_MODE", mode)
    monkeypatch.setenv("ASSET_RETRIEVAL_BACKEND", asset_retrieval_backend)
    for name, enabled in switch_values.items():
        monkeypatch.setenv(name, "true" if enabled else "false")
    if rodin_key:
        monkeypatch.setenv("RODIN_API_KEY", rodin_key)
    else:
        monkeypatch.delenv("RODIN_API_KEY", raising=False)
    if sketchfab_key:
        monkeypatch.setenv("SKETCHFAB_API_KEY", sketchfab_key)
    else:
        monkeypatch.delenv("SKETCHFAB_API_KEY", raising=False)
    monkeypatch.setattr(tool_registry.runtime, "get_blender_mode", lambda: mode)
    monkeypatch.setattr(
        tool_registry.runtime,
        "parse_env_bool",
        lambda name, default=False: switch_values.get(name, default),
    )
    monkeypatch.setattr(tool_registry.runtime, "is_hunyuan_tool_enabled", lambda: hunyuan)
    monkeypatch.setattr(tool_registry.runtime, "is_polyhaven_tool_enabled", lambda: polyhaven)
    monkeypatch.setattr(tool_registry.runtime, "is_rodin_tool_enabled", lambda: rodin)
    monkeypatch.setattr(tool_registry.runtime, "is_trellis2_tool_enabled", lambda: trellis2)
    monkeypatch.setattr(
        tool_registry.runtime,
        "get_retrieval_provider",
        lambda: asset_retrieval_backend,
    )
    monkeypatch.setattr(
        tool_registry.runtime,
        "is_retrieval_tool_enabled",
        lambda: asset_retrieval_backend != "disabled",
    )
    monkeypatch.setattr(
        tool_registry.runtime,
        "is_objaverse_retrieval_tool_enabled",
        lambda: asset_retrieval_backend == "objaverse",
    )
    monkeypatch.setattr(
        tool_registry.runtime,
        "is_scenesmith_retrieval_provider",
        lambda: asset_retrieval_backend == "scenesmith",
    )
    monkeypatch.setattr(
        tool_registry.runtime,
        "is_scenesmith_hssd_tool_enabled",
        lambda: asset_retrieval_backend == "scenesmith" and scenesmith_hssd,
    )
    monkeypatch.setattr(
        tool_registry.runtime,
        "is_scenesmith_ambientcg_tool_enabled",
        lambda: scenesmith_ambientcg,
    )
    monkeypatch.setattr(tool_registry.runtime, "is_infinigen_tool_enabled", lambda: infinigen)
    monkeypatch.setattr(
        tool_registry.runtime, "is_sam_reconstruct_tool_enabled", lambda: sam_reconstruct
    )
    monkeypatch.setattr(tool_registry.runtime, "is_sketchfab_tool_enabled", lambda: sketchfab)
    monkeypatch.setattr(tool_registry.runtime, "get_rodin_api_key", lambda: rodin_key)
    monkeypatch.setattr(tool_registry.runtime, "get_sketchfab_api_key", lambda: sketchfab_key)
    monkeypatch.setattr(
        tool_registry.runtime, "probe_sketchfab_api", lambda _logger: sketchfab_reachable
    )
    monkeypatch.setattr(
        tool_registry.runtime,
        "probe_conditional_services",
        lambda _logger: {
            "trellis2": True,
            "objaverse_retrieval": asset_retrieval_backend == "objaverse",
            "scenesmith_hssd": scenesmith_hssd_service,
            "scenesmith_ambientcg": scenesmith_ambientcg_service,
            "pcg_integrator": True,
            "sam_reconstruct": sam_reconstruct_service,
        },
    )


@pytest.mark.parametrize(
    ("hunyuan", "rodin", "trellis2"),
    [
        (True, False, True),
        (True, True, False),
        (False, True, True),
        (True, True, True),
    ],
)
def test_register_mcp_tools_fails_on_generator_conflict(
    monkeypatch, hunyuan, rodin, trellis2
):
    _reset_registry_state(monkeypatch)
    _configure_runtime(monkeypatch, hunyuan=hunyuan, rodin=rodin, trellis2=trellis2)

    with pytest.raises(
        RuntimeError,
        match="ENABLE_RODIN, ENABLE_TRELLIS2, and ENABLE_HUNYUAN are mutually exclusive",
    ):
        tool_registry.register_mcp_tools(FakeMCP(), logging.getLogger(__name__))


def test_register_mcp_tools_fails_on_retrieval_sketchfab_conflict(monkeypatch):
    _reset_registry_state(monkeypatch)
    _configure_runtime(
        monkeypatch,
        retrieval=True,
        sketchfab=True,
        sketchfab_key="configured",
    )

    with pytest.raises(RuntimeError, match="ASSET_RETRIEVAL_BACKEND"):
        tool_registry.register_mcp_tools(FakeMCP(), logging.getLogger(__name__))


def test_register_mcp_tools_skips_sketchfab_without_key(monkeypatch):
    _reset_registry_state(monkeypatch)
    _configure_runtime(monkeypatch, retrieval=True, sketchfab=True, sketchfab_key="")
    mcp = FakeMCP()

    enabled = tool_registry.register_mcp_tools(mcp, logging.getLogger(__name__))

    assert "search_3d_assets_by_text" in enabled
    assert "import_retrieved_asset" in enabled
    assert "search_sketchfab_models" not in enabled
    assert "get_sketchfab_model_preview" not in enabled
    assert "download_sketchfab_model" not in enabled


def test_register_mcp_tools_skips_sketchfab_when_api_unreachable(monkeypatch):
    _reset_registry_state(monkeypatch)
    _configure_runtime(
        monkeypatch,
        sketchfab=True,
        sketchfab_key="configured",
        sketchfab_reachable=False,
    )
    mcp = FakeMCP()

    enabled = tool_registry.register_mcp_tools(mcp, logging.getLogger(__name__))

    assert "search_sketchfab_models" not in enabled
    assert "get_sketchfab_model_preview" not in enabled
    assert "download_sketchfab_model" not in enabled


def test_register_mcp_tools_allows_retrieval_when_sketchfab_api_unreachable(monkeypatch):
    _reset_registry_state(monkeypatch)
    _configure_runtime(
        monkeypatch,
        retrieval=True,
        sketchfab=True,
        sketchfab_key="configured",
        sketchfab_reachable=False,
    )
    mcp = FakeMCP()

    enabled = tool_registry.register_mcp_tools(mcp, logging.getLogger(__name__))

    assert "search_3d_assets_by_text" in enabled
    assert "import_retrieved_asset" in enabled
    assert "search_sketchfab_models" not in enabled


def test_register_mcp_tools_respects_retrieval_and_infinigen_switches(monkeypatch):
    _reset_registry_state(monkeypatch)
    _configure_runtime(monkeypatch, retrieval=False, infinigen=False)
    mcp = FakeMCP()

    enabled = tool_registry.register_mcp_tools(mcp, logging.getLogger(__name__))

    assert "search_3d_assets_by_text" not in enabled
    assert "import_retrieved_asset" not in enabled
    assert "get_infinigen_available_assets" not in enabled
    assert "generate_infinigen_assets" not in enabled
    assert "get_viewport_screenshot" not in enabled
    assert "import_blend_contents" in enabled
    assert "observe_scene_global" in enabled
    assert "delete_objects" in enabled
    assert "get_session_persistence_status" not in enabled


def test_register_mcp_tools_skips_polyhaven_when_disabled(monkeypatch):
    _reset_registry_state(monkeypatch)
    _configure_runtime(monkeypatch, polyhaven=False)
    mcp = FakeMCP()

    enabled = tool_registry.register_mcp_tools(mcp, logging.getLogger(__name__))

    assert "search_polyhaven_assets" not in enabled
    assert "download_polyhaven_asset" not in enabled
    assert "set_texture" not in enabled


def test_register_mcp_tools_enables_scenesmith_retrieval_tools_when_provider_matches(monkeypatch):
    _reset_registry_state(monkeypatch)
    _configure_runtime(
        monkeypatch,
        retrieval=True,
        retrieval_provider="scenesmith",
        scenesmith_hssd=True,
        scenesmith_ambientcg=True,
    )
    mcp = FakeMCP()

    enabled = tool_registry.register_mcp_tools(mcp, logging.getLogger(__name__))

    assert "search_3d_assets_by_text" not in enabled
    assert "import_retrieved_asset" not in enabled
    assert "search_hssd_assets" in enabled
    assert "import_hssd_asset" in enabled
    assert "search_ambientcg_materials" in enabled
    assert "apply_ambientcg_material" in enabled


def test_register_mcp_tools_respects_independent_scenesmith_switches(monkeypatch):
    _reset_registry_state(monkeypatch)
    _configure_runtime(
        monkeypatch,
        retrieval=True,
        retrieval_provider="scenesmith",
        scenesmith_hssd=True,
        scenesmith_ambientcg=False,
    )
    mcp = FakeMCP()

    enabled = tool_registry.register_mcp_tools(mcp, logging.getLogger(__name__))

    assert "search_3d_assets_by_text" not in enabled
    assert "import_retrieved_asset" not in enabled
    assert "search_hssd_assets" in enabled
    assert "import_hssd_asset" in enabled
    assert "search_ambientcg_materials" not in enabled
    assert "apply_ambientcg_material" not in enabled


def test_register_mcp_tools_skips_unhealthy_scenesmith_subservice(monkeypatch):
    _reset_registry_state(monkeypatch)
    _configure_runtime(
        monkeypatch,
        retrieval=True,
        retrieval_provider="scenesmith",
        scenesmith_hssd=True,
        scenesmith_ambientcg=True,
        scenesmith_ambientcg_service=False,
    )
    mcp = FakeMCP()

    enabled = tool_registry.register_mcp_tools(mcp, logging.getLogger(__name__))

    assert "search_3d_assets_by_text" not in enabled
    assert "import_retrieved_asset" not in enabled
    assert "search_hssd_assets" in enabled
    assert "import_hssd_asset" in enabled
    assert "search_ambientcg_materials" not in enabled
    assert "apply_ambientcg_material" not in enabled


def test_register_mcp_tools_enables_ambientcg_when_objaverse_retrieval_is_selected(
    monkeypatch,
):
    _reset_registry_state(monkeypatch)
    _configure_runtime(
        monkeypatch,
        retrieval=True,
        retrieval_provider="objaverse",
        scenesmith_ambientcg=True,
    )
    mcp = FakeMCP()

    enabled = tool_registry.register_mcp_tools(mcp, logging.getLogger(__name__))

    assert "search_3d_assets_by_text" in enabled
    assert "import_retrieved_asset" in enabled
    assert "search_hssd_assets" not in enabled
    assert "import_hssd_asset" not in enabled
    assert "search_ambientcg_materials" in enabled
    assert "apply_ambientcg_material" in enabled


def test_register_mcp_tools_enables_ambientcg_without_asset_retrieval_backend(monkeypatch):
    _reset_registry_state(monkeypatch)
    _configure_runtime(
        monkeypatch,
        retrieval=False,
        scenesmith_ambientcg=True,
    )
    mcp = FakeMCP()

    enabled = tool_registry.register_mcp_tools(mcp, logging.getLogger(__name__))

    assert "search_3d_assets_by_text" not in enabled
    assert "import_retrieved_asset" not in enabled
    assert "search_hssd_assets" not in enabled
    assert "import_hssd_asset" not in enabled
    assert "search_ambientcg_materials" in enabled
    assert "apply_ambientcg_material" in enabled


def test_register_mcp_tools_skips_scenesmith_retrieval_tools_for_legacy_provider(monkeypatch):
    _reset_registry_state(monkeypatch)
    _configure_runtime(
        monkeypatch,
        retrieval=True,
        retrieval_provider="objaverse",
    )
    mcp = FakeMCP()

    enabled = tool_registry.register_mcp_tools(mcp, logging.getLogger(__name__))

    assert "search_hssd_assets" not in enabled
    assert "import_hssd_asset" not in enabled
    assert "search_ambientcg_materials" not in enabled
    assert "apply_ambientcg_material" not in enabled


def test_register_mcp_tools_enables_viewport_screenshot_in_local_client(monkeypatch):
    _reset_registry_state(monkeypatch)
    _configure_runtime(monkeypatch, mode="local-client")
    mcp = FakeMCP()

    enabled = tool_registry.register_mcp_tools(mcp, logging.getLogger(__name__))

    assert "get_viewport_screenshot" in enabled
    assert "render_from_objects" not in enabled
    assert "render_from_camera" not in enabled
    assert "camera_set_pose" not in enabled
    assert "camera_observe" not in enabled
    assert "camera_act" not in enabled
    assert "observe_scene_global" not in enabled
    assert "undo_last_snapshot" not in enabled


def test_register_mcp_tools_disables_viewport_screenshot_in_headless(monkeypatch):
    _reset_registry_state(monkeypatch)
    _configure_runtime(monkeypatch, mode="headless")
    mcp = FakeMCP()

    enabled = tool_registry.register_mcp_tools(mcp, logging.getLogger(__name__))

    assert "get_viewport_screenshot" not in enabled
    assert "render_from_objects" in enabled
    assert "render_from_camera" in enabled
    assert "camera_set_pose" in enabled
    assert "camera_observe" in enabled
    assert "camera_act" in enabled
    assert "observe_scene_global" in enabled
    assert "undo_last_snapshot" in enabled


def test_register_mcp_tools_enables_sam_reconstruct_when_ready(monkeypatch):
    _reset_registry_state(monkeypatch)
    _configure_runtime(monkeypatch, sam_reconstruct=True, sam_reconstruct_service=True)
    mcp = FakeMCP()

    enabled = tool_registry.register_mcp_tools(mcp, logging.getLogger(__name__))

    assert "reconstruct_full_scene" in enabled


def test_register_mcp_tools_skips_sam_reconstruct_when_service_unhealthy(monkeypatch):
    _reset_registry_state(monkeypatch)
    _configure_runtime(monkeypatch, sam_reconstruct=True, sam_reconstruct_service=False)
    mcp = FakeMCP()

    enabled = tool_registry.register_mcp_tools(mcp, logging.getLogger(__name__))

    assert "reconstruct_full_scene" not in enabled
