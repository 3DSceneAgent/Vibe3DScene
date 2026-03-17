import logging

import requests

from mcp_server import runtime


def test_rodin_gate_enabled_in_local_client_mode(monkeypatch):
    monkeypatch.setenv("BLENDER_MODE", "local-client")
    monkeypatch.setenv("ENABLE_RODIN", "true")

    assert runtime.is_rodin_tool_enabled() is True


def test_rodin_gate_enabled_in_headless_mode(monkeypatch):
    monkeypatch.setenv("BLENDER_MODE", "headless")
    monkeypatch.setenv("ENABLE_RODIN", "1")

    assert runtime.is_rodin_tool_enabled() is True


def test_rodin_gate_disabled_outside_supported_modes(monkeypatch):
    monkeypatch.setenv("BLENDER_MODE", "other")
    monkeypatch.setenv("ENABLE_RODIN", "true")

    assert runtime.is_rodin_tool_enabled() is False


def test_sam_reconstruct_gate_enabled(monkeypatch):
    monkeypatch.setenv("ENABLE_SAM_RECONSTRUCT", "true")

    assert runtime.is_sam_reconstruct_tool_enabled() is True


def test_sam_reconstruct_gate_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_SAM_RECONSTRUCT", raising=False)

    assert runtime.is_sam_reconstruct_tool_enabled() is False


def test_polyhaven_gate_enabled_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_POLYHAVEN", raising=False)

    assert runtime.is_polyhaven_tool_enabled() is True


def test_polyhaven_gate_respects_explicit_toggle(monkeypatch):
    monkeypatch.setenv("ENABLE_POLYHAVEN", "false")

    assert runtime.is_polyhaven_tool_enabled() is False


def test_asset_retrieval_gate_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ASSET_RETRIEVAL_BACKEND", raising=False)

    assert runtime.is_retrieval_tool_enabled() is False


def test_asset_retrieval_gate_enabled_for_objaverse(monkeypatch):
    monkeypatch.setenv("ASSET_RETRIEVAL_BACKEND", "objaverse")

    assert runtime.is_retrieval_tool_enabled() is True
    assert runtime.is_objaverse_retrieval_tool_enabled() is True
    assert runtime.is_scenesmith_hssd_tool_enabled() is False


def test_scenesmith_hssd_gate_enabled_for_scenesmith_backend(monkeypatch):
    monkeypatch.setenv("ASSET_RETRIEVAL_BACKEND", "scenesmith")

    assert runtime.is_retrieval_tool_enabled() is True
    assert runtime.is_objaverse_retrieval_tool_enabled() is False
    assert runtime.is_scenesmith_hssd_tool_enabled() is True


def test_scenesmith_ambientcg_gate_disabled_by_default(monkeypatch):
    monkeypatch.setenv("ASSET_RETRIEVAL_BACKEND", "scenesmith")
    monkeypatch.delenv("ENABLE_AMBIENTCG", raising=False)

    assert runtime.is_scenesmith_ambientcg_tool_enabled() is False


def test_scenesmith_ambientcg_gate_respects_explicit_toggle(monkeypatch):
    monkeypatch.setenv("ASSET_RETRIEVAL_BACKEND", "scenesmith")
    monkeypatch.setenv("ENABLE_AMBIENTCG", "true")

    assert runtime.is_scenesmith_ambientcg_tool_enabled() is True


def test_probe_sketchfab_api_succeeds_on_http_response(monkeypatch):
    class FakeResponse:
        status_code = 401

    runtime.reset_sketchfab_api_probe_cache()
    monkeypatch.setattr(runtime.requests, "get", lambda *_args, **_kwargs: FakeResponse())

    assert runtime.probe_sketchfab_api(logging.getLogger(__name__)) is True
    assert runtime.get_cached_sketchfab_api_reachability() is True


def test_probe_sketchfab_api_fails_on_connection_error(monkeypatch):
    runtime.reset_sketchfab_api_probe_cache()

    def raise_connection_error(*_args, **_kwargs):
        raise requests.exceptions.ConnectionError("network down")

    monkeypatch.setattr(runtime.requests, "get", raise_connection_error)

    assert runtime.probe_sketchfab_api(logging.getLogger(__name__)) is False
    assert runtime.get_cached_sketchfab_api_reachability() is False
