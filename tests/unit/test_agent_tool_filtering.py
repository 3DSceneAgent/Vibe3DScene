from langchain_core.messages import AIMessage, SystemMessage

from scene_agent.agent.nodes import agent_node


class FakeLLM:
    def __init__(self, response: AIMessage) -> None:
        self.response = response
        self.invocations = []

    def invoke(self, messages):
        self.invocations.append(messages)
        return self.response


def test_agent_node_filters_unavailable_tool_calls():
    llm = FakeLLM(
        AIMessage(
            content="",
            tool_calls=[
                {"name": "get_scene_info", "args": {}, "id": "tc-1", "type": "tool_call"},
                {"name": "search_3d_assets_by_text", "args": {}, "id": "tc-2", "type": "tool_call"},
            ],
        )
    )

    result = agent_node({"messages": []}, llm, ["get_scene_info"])
    message = result["messages"][0]

    assert [call["name"] for call in message.tool_calls] == ["get_scene_info"]
    runtime_constraints = [
        msg.content
        for msg in llm.invocations[0]
        if isinstance(msg, SystemMessage) and "CURRENT_AVAILABLE_TOOLS" in str(msg.content)
    ]
    assert runtime_constraints


def test_agent_node_injects_fallback_text_when_all_tool_calls_dropped():
    llm = FakeLLM(
        AIMessage(
            content="",
            tool_calls=[
                {"name": "search_3d_assets_by_text", "args": {}, "id": "tc-3", "type": "tool_call"},
            ],
        )
    )

    result = agent_node({"messages": []}, llm, ["get_scene_info"])
    message = result["messages"][0]

    assert message.tool_calls == []
    assert "skipped unavailable tool calls" in message.content.lower()


def test_agent_node_respects_runtime_enabled_tool_names():
    llm = FakeLLM(
        AIMessage(
            content="",
            tool_calls=[
                {"name": "get_scene_info", "args": {}, "id": "tc-4", "type": "tool_call"},
                {"name": "camera_observe", "args": {}, "id": "tc-5", "type": "tool_call"},
            ],
        )
    )

    result = agent_node(
        {"messages": [], "enabled_tool_names": ["camera_observe"]},
        llm,
        ["get_scene_info", "camera_observe"],
    )
    message = result["messages"][0]

    assert [call["name"] for call in message.tool_calls] == ["camera_observe"]


def test_agent_node_soft_disables_sam_reconstruct_without_current_request_image():
    llm = FakeLLM(
        AIMessage(
            content="",
            tool_calls=[
                {"name": "reconstruct_full_scene", "args": {}, "id": "tc-6", "type": "tool_call"},
            ],
        )
    )

    result = agent_node({"messages": []}, llm, ["reconstruct_full_scene"])
    message = result["messages"][0]

    assert message.tool_calls == []
    runtime_constraints = [
        str(msg.content)
        for msg in llm.invocations[0]
        if isinstance(msg, SystemMessage) and "CURRENT_AVAILABLE_TOOLS" in str(msg.content)
    ]
    assert runtime_constraints
    assert "reconstruct_full_scene" not in runtime_constraints[0]


def test_agent_node_keeps_sam_reconstruct_when_exactly_one_image_is_attached():
    llm = FakeLLM(
        AIMessage(
            content="",
            tool_calls=[
                {"name": "reconstruct_full_scene", "args": {}, "id": "tc-7", "type": "tool_call"},
            ],
        )
    )

    result = agent_node(
        {"messages": [], "attached_image_ids": ["asset-1"]},
        llm,
        ["reconstruct_full_scene"],
    )
    message = result["messages"][0]

    assert [call["name"] for call in message.tool_calls] == ["reconstruct_full_scene"]
