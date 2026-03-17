from scene_agent.utils import tool_service_endpoints as endpoints


def test_shared_tool_service_host_applies_to_all_services(monkeypatch):
    monkeypatch.setenv("ASSET_RETRIEVAL_BACKEND", "objaverse")
    monkeypatch.delenv("TRELLIS2_HOST", raising=False)
    monkeypatch.delenv("OBJAVERSE_HOST", raising=False)
    monkeypatch.delenv("SCENESMITH_COMPAT_HOST", raising=False)
    monkeypatch.delenv("SCENESMITH_COMPAT_PORT", raising=False)
    monkeypatch.delenv("INFINIGEN_HOST", raising=False)
    monkeypatch.delenv("SAM_HOST", raising=False)
    monkeypatch.delenv("SAM_PORT", raising=False)
    monkeypatch.setenv("TOOL_SERVICE_HOST", "10.0.0.9")

    assert endpoints.get_trellis2_base_url() == "http://10.0.0.9:8001"
    assert endpoints.get_retrieval_base_url() == "http://10.0.0.9:8002"
    assert endpoints.get_infinigen_base_url() == "http://10.0.0.9:8003"
    assert endpoints.get_sam_http_base_url() == "http://10.0.0.9:8004"


def test_service_specific_overrides_take_precedence(monkeypatch):
    monkeypatch.setenv("ASSET_RETRIEVAL_BACKEND", "objaverse")
    monkeypatch.setenv("TOOL_SERVICE_HOST", "10.0.0.9")
    monkeypatch.setenv("OBJAVERSE_HOST", "objaverse.internal")
    monkeypatch.setenv("SAM_HOST", "sam.internal")
    monkeypatch.setenv("SAM_PORT", "8123")

    assert endpoints.get_retrieval_base_url() == "http://objaverse.internal:8002"
    assert endpoints.get_sam_http_base_url() == "http://sam.internal:8123"


def test_blank_service_specific_values_fall_back_to_shared_host(monkeypatch):
    monkeypatch.setenv("ASSET_RETRIEVAL_BACKEND", "objaverse")
    monkeypatch.setenv("TOOL_SERVICE_HOST", "tools-box")
    monkeypatch.setenv("TRELLIS2_HOST", "   ")
    monkeypatch.setenv("SAM_HOST", "   ")
    monkeypatch.setenv("SAM_PORT", "   ")

    assert endpoints.get_trellis2_base_url() == "http://tools-box:8001"
    assert endpoints.get_sam_http_base_url() == "http://tools-box:8004"


def test_scenesmith_compat_uses_dedicated_default_port(monkeypatch):
    monkeypatch.setenv("ASSET_RETRIEVAL_BACKEND", "scenesmith")
    monkeypatch.delenv("SCENESMITH_COMPAT_HOST", raising=False)
    monkeypatch.delenv("SCENESMITH_COMPAT_PORT", raising=False)
    monkeypatch.setenv("TOOL_SERVICE_HOST", "10.0.0.9")

    assert endpoints.get_retrieval_base_url() == "http://10.0.0.9:8005"


def test_scenesmith_compat_specific_overrides_take_precedence(monkeypatch):
    monkeypatch.setenv("ASSET_RETRIEVAL_BACKEND", "scenesmith")
    monkeypatch.setenv("TOOL_SERVICE_HOST", "10.0.0.9")
    monkeypatch.setenv("OBJAVERSE_HOST", "legacy-objaverse.internal")
    monkeypatch.setenv("OBJAVERSE_PORT", "8124")
    monkeypatch.setenv("SCENESMITH_COMPAT_HOST", "scenesmith.internal")
    monkeypatch.setenv("SCENESMITH_COMPAT_PORT", "8125")

    assert endpoints.get_retrieval_base_url() == "http://scenesmith.internal:8125"


def test_scenesmith_compat_does_not_fall_back_to_objaverse_envs(monkeypatch):
    monkeypatch.setenv("ASSET_RETRIEVAL_BACKEND", "scenesmith")
    monkeypatch.delenv("SCENESMITH_COMPAT_HOST", raising=False)
    monkeypatch.delenv("SCENESMITH_COMPAT_PORT", raising=False)
    monkeypatch.setenv("OBJAVERSE_HOST", "legacy-objaverse.internal")
    monkeypatch.setenv("OBJAVERSE_PORT", "8124")
    monkeypatch.setenv("TOOL_SERVICE_HOST", "10.0.0.9")

    assert endpoints.get_retrieval_base_url() == "http://10.0.0.9:8005"
