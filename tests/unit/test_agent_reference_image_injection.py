from langchain_core.messages import AIMessage, HumanMessage

from scene_agent.agent.nodes import agent_node, builder_agent_node, verifier_camera_agent_node


class FakeLLM:
    def __init__(self) -> None:
        self.invocations: list[list] = []

    def invoke(self, messages):
        self.invocations.append(messages)
        return AIMessage(content="ok")


def _latest_human(messages: list) -> HumanMessage:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return message
    raise AssertionError("No HumanMessage found in prompt payload")


def _request_image_state(message: str, *, catalog: dict[str, dict], selected_keys: list[str]) -> dict:
    return {
        "messages": [HumanMessage(content=message)],
        "reference_image_catalog": catalog,
        "request_reference_image_keys": selected_keys,
    }


def test_agent_node_injects_reference_images_into_latest_user_message(monkeypatch):
    monkeypatch.setattr(
        "scene_agent.agent.nodes.shared._path_to_data_url",
        lambda path: f"data:image/png;base64,{path.split('/')[-1]}",
    )

    llm = FakeLLM()
    state = _request_image_state(
        "describe the image",
        catalog={
            "front_view": {
                "asset_id": "asset-a",
                "stored_path": "/tmp/a.png",
                "caption": "front render",
                "source_turn_at": "2026-01-01T00:00:00",
                "created_at": "2026-01-01T00:00:00",
                "last_used_at": None,
                "use_count": 0,
            },
            "side_view": {
                "asset_id": "asset-b",
                "stored_path": "/tmp/b.png",
                "caption": "side render",
                "source_turn_at": "2026-01-01T00:00:00",
                "created_at": "2026-01-01T00:00:00",
                "last_used_at": None,
                "use_count": 0,
            },
        },
        selected_keys=["front_view", "side_view"],
    )

    agent_node(state, llm, ["get_scene_info"])

    prompt_messages = llm.invocations[0]
    latest_human = _latest_human(prompt_messages)
    assert isinstance(latest_human.content, list)
    assert latest_human.content[0] == {"type": "text", "text": "describe the image"}
    image_items = [
        item for item in latest_human.content
        if isinstance(item, dict) and item.get("type") == "image_url"
    ]
    assert len(image_items) == 2
    assert image_items[0]["image_url"]["url"].startswith("data:image/png;base64,")
    # Prompt-level injection should not mutate persisted graph state.
    assert isinstance(state["messages"][0].content, str)


def test_builder_node_injects_reference_images_every_round(monkeypatch):
    monkeypatch.setattr(
        "scene_agent.agent.nodes.shared._path_to_data_url",
        lambda _path: "data:image/png;base64,ref",
    )

    llm = FakeLLM()
    state = _request_image_state(
        "continue building scene from reference",
        catalog={
            "reference": {
                "asset_id": "asset-ref",
                "stored_path": "/tmp/ref.png",
                "caption": "reference",
                "source_turn_at": "2026-01-01T00:00:00",
                "created_at": "2026-01-01T00:00:00",
                "last_used_at": None,
                "use_count": 0,
            }
        },
        selected_keys=["reference"],
    )

    builder_agent_node(state, llm, ["get_scene_info"])

    prompt_messages = llm.invocations[0]
    latest_human = _latest_human(prompt_messages)
    assert isinstance(latest_human.content, list)
    image_items = [
        item for item in latest_human.content
        if isinstance(item, dict) and item.get("type") == "image_url"
    ]
    assert len(image_items) == 1


def test_verifier_camera_agent_injects_request_scoped_reference_images(monkeypatch):
    monkeypatch.setattr(
        "scene_agent.agent.nodes.shared._path_to_data_url",
        lambda _path: "data:image/png;base64,ref",
    )

    llm = FakeLLM()
    state = _request_image_state(
        "check camera view",
        catalog={
            "verify_ref": {
                "asset_id": "asset-ref",
                "stored_path": "/tmp/ref.png",
                "caption": "verification",
                "source_turn_at": "2026-01-01T00:00:00",
                "created_at": "2026-01-01T00:00:00",
                "last_used_at": None,
                "use_count": 0,
            }
        },
        selected_keys=["verify_ref"],
    )

    verifier_camera_agent_node(state, llm, ["camera_observe"])

    prompt_messages = llm.invocations[0]
    latest_human = _latest_human(prompt_messages)
    assert isinstance(latest_human.content, list)
    image_items = [
        item for item in latest_human.content
        if isinstance(item, dict) and item.get("type") == "image_url"
    ]
    assert len(image_items) == 1


def test_agent_node_skips_injection_when_image_conversion_fails(monkeypatch):
    monkeypatch.setattr("scene_agent.agent.nodes.shared._path_to_data_url", lambda _path: None)

    llm = FakeLLM()
    state = _request_image_state(
        "describe this",
        catalog={
            "missing": {
                "asset_id": "asset-missing",
                "stored_path": "/missing.png",
                "caption": "missing",
                "source_turn_at": "2026-01-01T00:00:00",
                "created_at": "2026-01-01T00:00:00",
                "last_used_at": None,
                "use_count": 0,
            }
        },
        selected_keys=["missing"],
    )

    agent_node(state, llm, ["get_scene_info"])

    prompt_messages = llm.invocations[0]
    latest_human = _latest_human(prompt_messages)
    assert latest_human.content == "describe this"
