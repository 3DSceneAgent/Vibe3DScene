from types import SimpleNamespace

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


def test_agent_node_injects_reference_images_into_latest_user_message(monkeypatch):
    class _Memory:
        def resolve_assets(self, *, thread_id: str, task_id: str, limit: int):
            assert thread_id == "thread-img-agent"
            assert task_id == "conversation"
            assert limit == 3
            return [
                SimpleNamespace(stored_path="/tmp/a.png"),
                SimpleNamespace(stored_path="/tmp/b.png"),
            ]

    monkeypatch.setattr("scene_agent.agent.nodes.shared.get_reference_image_memory", lambda: _Memory())
    monkeypatch.setattr(
        "scene_agent.agent.nodes.shared._path_to_data_url",
        lambda path: f"data:image/png;base64,{path.split('/')[-1]}",
    )

    llm = FakeLLM()
    state = {
        "thread_id": "thread-img-agent",
        "task_id": "conversation",
        "messages": [HumanMessage(content="describe the image")],
    }

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
    class _Memory:
        def resolve_assets(self, *, thread_id: str, task_id: str, limit: int):
            assert thread_id == "thread-img-builder"
            assert task_id == "plan-task"
            assert limit == 3
            return [SimpleNamespace(stored_path="/tmp/ref.png")]

    monkeypatch.setattr("scene_agent.agent.nodes.shared.get_reference_image_memory", lambda: _Memory())
    monkeypatch.setattr(
        "scene_agent.agent.nodes.shared._path_to_data_url",
        lambda _path: "data:image/png;base64,ref",
    )

    llm = FakeLLM()
    state = {
        "thread_id": "thread-img-builder",
        "task_id": "plan-task",
        "messages": [HumanMessage(content="continue building scene from reference")],
    }

    builder_agent_node(state, llm, ["get_scene_info"])

    prompt_messages = llm.invocations[0]
    latest_human = _latest_human(prompt_messages)
    assert isinstance(latest_human.content, list)
    image_items = [
        item for item in latest_human.content
        if isinstance(item, dict) and item.get("type") == "image_url"
    ]
    assert len(image_items) == 1


def test_verifier_camera_agent_does_not_inject_reference_images(monkeypatch):
    class _Memory:
        def resolve_assets(self, *, thread_id: str, task_id: str, limit: int):
            _ = (thread_id, task_id, limit)
            return [SimpleNamespace(stored_path="/tmp/ref.png")]

    monkeypatch.setattr("scene_agent.agent.nodes.shared.get_reference_image_memory", lambda: _Memory())
    monkeypatch.setattr(
        "scene_agent.agent.nodes.shared._path_to_data_url",
        lambda _path: "data:image/png;base64,ref",
    )

    llm = FakeLLM()
    state = {
        "thread_id": "thread-img-verifier",
        "task_id": "verify-task",
        "messages": [HumanMessage(content="check camera view")],
    }

    verifier_camera_agent_node(state, llm, ["camera_observe"])

    prompt_messages = llm.invocations[0]
    latest_human = _latest_human(prompt_messages)
    assert latest_human.content == "check camera view"


def test_agent_node_skips_injection_when_image_conversion_fails(monkeypatch):
    class _Memory:
        def resolve_assets(self, *, thread_id: str, task_id: str, limit: int):
            _ = (thread_id, task_id, limit)
            return [SimpleNamespace(stored_path="/missing.png")]

    monkeypatch.setattr("scene_agent.agent.nodes.shared.get_reference_image_memory", lambda: _Memory())
    monkeypatch.setattr("scene_agent.agent.nodes.shared._path_to_data_url", lambda _path: None)

    llm = FakeLLM()
    state = {
        "thread_id": "thread-img-missing",
        "task_id": "conversation",
        "messages": [HumanMessage(content="describe this")],
    }

    agent_node(state, llm, ["get_scene_info"])

    prompt_messages = llm.invocations[0]
    latest_human = _latest_human(prompt_messages)
    assert latest_human.content == "describe this"
