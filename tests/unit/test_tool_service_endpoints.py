from scene_agent.utils import tool_service_endpoints as endpoints


def test_shared_tool_service_host_applies_to_all_services(monkeypatch):
    monkeypatch.delenv("TRELLIS2_HOST", raising=False)
    monkeypatch.delenv("RETRIEVAL_API_HOST", raising=False)
    monkeypatch.delenv("INFINIGEN_HOST", raising=False)
    monkeypatch.delenv("SAM_HTTP_BASE_URL", raising=False)
    monkeypatch.setenv("TOOL_SERVICE_HOST", "10.0.0.9")

    assert endpoints.get_trellis2_base_url() == "http://10.0.0.9:8001"
    assert endpoints.get_retrieval_base_url() == "http://10.0.0.9:8002"
    assert endpoints.get_infinigen_base_url() == "http://10.0.0.9:8003"
    assert endpoints.get_sam_http_base_url() == "http://10.0.0.9:8004"


def test_service_specific_overrides_take_precedence(monkeypatch):
    monkeypatch.setenv("TOOL_SERVICE_HOST", "10.0.0.9")
    monkeypatch.setenv("RETRIEVAL_API_HOST", "retrieval.internal")
    monkeypatch.setenv("SAM_HTTP_BASE_URL", "https://sam.internal/custom")

    assert endpoints.get_retrieval_base_url() == "http://retrieval.internal:8002"
    assert endpoints.get_sam_http_base_url() == "https://sam.internal/custom"


def test_blank_service_specific_values_fall_back_to_shared_host(monkeypatch):
    monkeypatch.setenv("TOOL_SERVICE_HOST", "tools-box")
    monkeypatch.setenv("TRELLIS2_HOST", "   ")
    monkeypatch.setenv("SAM_HTTP_BASE_URL", "")

    assert endpoints.get_trellis2_base_url() == "http://tools-box:8001"
    assert endpoints.get_sam_http_base_url() == "http://tools-box:8004"
