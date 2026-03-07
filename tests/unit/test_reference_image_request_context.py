from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import HumanMessage

import scene_agent.agent.nodes.shared as shared
from scene_agent.agent.nodes import prepare_reference_context_node, sync_reference_catalog_node


def _catalog_entry(asset_id: str, stored_path: str, *, caption: str = "") -> dict:
    return {
        "asset_id": asset_id,
        "stored_path": stored_path,
        "caption": caption,
        "source_turn_at": "2026-01-01T00:00:00",
        "created_at": "2026-01-01T00:00:00",
        "last_used_at": None,
        "use_count": 0,
    }


def test_prepare_reference_context_uses_attached_image_ids_in_input_order(monkeypatch):
    assets_by_id = {
        "asset-a": SimpleNamespace(id="asset-a", filename="chair.png", stored_path="/tmp/chair.png"),
        "asset-b": SimpleNamespace(id="asset-b", filename="lamp.png", stored_path="/tmp/lamp.png"),
    }

    class _Memory:
        def get_assets_by_ids(self, thread_id: str, asset_ids: list[str]):
            assert thread_id == "thread-images"
            return [assets_by_id[asset_id] for asset_id in asset_ids if asset_id in assets_by_id]

    monkeypatch.setattr("scene_agent.agent.nodes.shared.get_reference_image_memory", lambda: _Memory())
    monkeypatch.setattr(
        "scene_agent.agent.nodes.shared._describe_reference_image_with_helper",
        lambda *, asset, provider_name, api_key: (asset.filename.split(".")[0], f"{asset.id}-caption"),
    )

    state = {
        "thread_id": "thread-images",
        "attached_image_ids": ["asset-b", "asset-a"],
        "messages": [HumanMessage(content="Use the uploaded images.")],
    }

    synced = sync_reference_catalog_node(state, provider_name="openai", api_key="test-key")
    result = prepare_reference_context_node(
        {**state, **synced},
        provider_name="openai",
        api_key="test-key",
    )

    assert result["request_reference_image_source"] == "attached"
    assert result["request_reference_image_keys"] == ["lamp", "chair"]
    catalog = result["reference_image_catalog"]
    assert catalog["lamp"]["asset_id"] == "asset-b"
    assert catalog["chair"]["asset_id"] == "asset-a"


def test_sync_reference_catalog_rehydrates_thread_assets_when_catalog_is_empty(monkeypatch):
    asset = SimpleNamespace(id="asset-chair", filename="chair.png", stored_path="/tmp/chair.png")

    class _Memory:
        def resolve_assets(self, *, thread_id: str, task_id: str | None = None):
            assert thread_id == "thread-images"
            assert task_id == "plan"
            return [asset]

    monkeypatch.setattr("scene_agent.agent.nodes.shared.get_reference_image_memory", lambda: _Memory())
    monkeypatch.setattr(
        "scene_agent.agent.nodes.shared._describe_reference_image_with_helper",
        lambda *, asset, provider_name, api_key: ("chair_ref", f"{asset.id}-caption"),
    )
    monkeypatch.setattr(
        "scene_agent.agent.nodes.shared._select_reference_image_with_helper",
        lambda **_kwargs: (True, "chair_ref", "helper_selected"),
    )

    state = {
        "thread_id": "thread-images",
        "task_id": "plan",
        "messages": [HumanMessage(content="Match the earlier chair reference.")],
    }

    synced = sync_reference_catalog_node(state)
    result = prepare_reference_context_node({**state, **synced})

    assert synced["reference_image_catalog"]["chair_ref"]["asset_id"] == "asset-chair"
    assert result["request_reference_image_keys"] == ["chair_ref"]
    assert result["request_reference_image_source"] == "memory_retrieved"


def test_sync_reference_catalog_falls_back_to_list_assets_when_resolve_assets_is_missing(monkeypatch):
    asset = SimpleNamespace(id="asset-chair", filename="chair.png", stored_path="/tmp/chair.png")

    class _Memory:
        def list_assets(self, thread_id: str):
            assert thread_id == "thread-images"
            return [asset]

    monkeypatch.setattr("scene_agent.agent.nodes.shared.get_reference_image_memory", lambda: _Memory())
    monkeypatch.setattr(
        "scene_agent.agent.nodes.shared._describe_reference_image_with_helper",
        lambda *, asset, provider_name, api_key: ("chair_ref", f"{asset.id}-caption"),
    )

    state = {
        "thread_id": "thread-images",
        "messages": [HumanMessage(content="Use the stored reference.")],
    }

    synced = sync_reference_catalog_node(state)

    assert synced["reference_image_catalog"]["chair_ref"]["asset_id"] == "asset-chair"
    assert synced["request_reference_image_keys"] == []
    assert synced["request_reference_image_source"] == "none"


def test_prepare_reference_context_selects_one_catalog_image_when_helper_requests_it(monkeypatch):
    monkeypatch.setattr(
        "scene_agent.agent.nodes.shared._select_reference_image_with_helper",
        lambda **_kwargs: (True, "chair_ref", "helper_selected"),
    )

    state = {
        "messages": [HumanMessage(content="Match the prior chair reference.")],
        "reference_image_catalog": {
            "chair_ref": _catalog_entry("asset-chair", "/tmp/chair.png", caption="wooden chair")
        },
    }

    result = prepare_reference_context_node(state)

    assert result["request_reference_image_keys"] == ["chair_ref"]
    assert result["request_reference_image_source"] == "memory_retrieved"
    updated = result["reference_image_catalog"]["chair_ref"]
    assert updated["use_count"] == 1
    assert isinstance(updated["last_used_at"], str) and updated["last_used_at"]


def test_prepare_reference_context_clears_request_images_when_helper_declines(monkeypatch):
    monkeypatch.setattr(
        "scene_agent.agent.nodes.shared._select_reference_image_with_helper",
        lambda **_kwargs: (False, None, "helper_declined"),
    )

    state = {
        "messages": [HumanMessage(content="Create a new unrelated scene.")],
        "reference_image_catalog": {
            "chair_ref": _catalog_entry("asset-chair", "/tmp/chair.png", caption="wooden chair")
        },
    }

    result = prepare_reference_context_node(state)

    assert result["request_reference_image_keys"] == []
    assert result["request_reference_image_source"] == "none"
    assert result["request_reference_image_reason"] == "helper_declined"


def test_prepare_reference_context_reuses_existing_key_for_duplicate_asset(monkeypatch):
    asset = SimpleNamespace(id="asset-chair", filename="chair-new.png", stored_path="/tmp/chair-new.png")

    class _Memory:
        def get_assets_by_ids(self, _thread_id: str, _asset_ids: list[str]):
            return [asset]

    monkeypatch.setattr("scene_agent.agent.nodes.shared.get_reference_image_memory", lambda: _Memory())

    calls = {"count": 0}

    def _unexpected_describe(**_kwargs):
        calls["count"] += 1
        return ("unexpected", "unexpected")

    monkeypatch.setattr("scene_agent.agent.nodes.shared._describe_reference_image_with_helper", _unexpected_describe)

    state = {
        "thread_id": "thread-images",
        "attached_image_ids": ["asset-chair"],
        "messages": [HumanMessage(content="Reuse the same chair image.")],
        "reference_image_catalog": {
            "chair_ref": _catalog_entry("asset-chair", "/tmp/chair.png", caption="wooden chair")
        },
    }

    synced = sync_reference_catalog_node(state)
    result = prepare_reference_context_node({**state, **synced})

    assert result["request_reference_image_keys"] == ["chair_ref"]
    assert len(synced["reference_image_catalog"]) == 1
    assert calls["count"] == 0


def test_sync_reference_catalog_suffixes_colliding_generated_names(monkeypatch):
    assets = [
        SimpleNamespace(id="asset-a", filename="chair-a.png", stored_path="/tmp/chair-a.png"),
        SimpleNamespace(id="asset-b", filename="chair-b.png", stored_path="/tmp/chair-b.png"),
    ]

    class _Memory:
        def get_assets_by_ids(self, _thread_id: str, _asset_ids: list[str]):
            return assets

    monkeypatch.setattr("scene_agent.agent.nodes.shared.get_reference_image_memory", lambda: _Memory())
    monkeypatch.setattr(
        "scene_agent.agent.nodes.shared._describe_reference_image_with_helper",
        lambda **_kwargs: ("chair_ref", "chair"),
    )

    state = {
        "thread_id": "thread-images",
        "attached_image_ids": ["asset-a", "asset-b"],
        "messages": [HumanMessage(content="Attach both chair images.")],
    }

    synced = sync_reference_catalog_node(state)
    result = prepare_reference_context_node({**state, **synced})

    assert result["request_reference_image_keys"] == ["chair_ref", "chair_ref_2"]
    assert sorted(result["reference_image_catalog"].keys()) == ["chair_ref", "chair_ref_2"]


def test_reference_image_name_helper_falls_back_to_filename(monkeypatch):
    asset = SimpleNamespace(id="asset-chair", filename="Chair Photo.png", stored_path="/tmp/chair.png")

    monkeypatch.setattr("scene_agent.agent.nodes.shared._path_to_data_url", lambda _path: "data:image/png;base64,abc")
    monkeypatch.setattr(
        "scene_agent.agent.nodes.shared._resolve_reference_image_helper_model",
        lambda **_kwargs: None,
    )

    name, caption = shared._describe_reference_image_with_helper(
        asset=asset,
        provider_name="openai",
        api_key="test-key",
    )

    assert name == "chair_photo"
    assert caption == ""


def test_reference_image_selection_helper_failure_does_not_auto_attach(monkeypatch):
    monkeypatch.setattr(
        "scene_agent.agent.nodes.shared._resolve_reference_image_helper_model",
        lambda **_kwargs: None,
    )

    should_attach, selected_name, reason = shared._select_reference_image_with_helper(
        latest_user_request="Please match the wooden chair proportions.",
        catalog={
            "chair_ref": _catalog_entry("asset-chair", "/tmp/chair.png", caption="wooden chair"),
            "lamp_ref": _catalog_entry("asset-lamp", "/tmp/lamp.png", caption="metal floor lamp"),
        },
        provider_name="openai",
        api_key="test-key",
    )

    assert should_attach is False
    assert selected_name is None
    assert reason == "helper_unavailable_no_auto_attach"
