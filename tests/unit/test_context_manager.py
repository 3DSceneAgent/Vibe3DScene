from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from scene_agent.agent.context_manager import build_projected_context


def test_build_projected_context_compacts_older_messages():
    state_messages = [HumanMessage(content=f"user-{idx}") for idx in range(8)]
    state_messages.append(ToolMessage(name="verification", content={"status": "mismatch"}, tool_call_id="verify-1"))
    state_messages.extend(AIMessage(content=f"assistant-{idx}") for idx in range(8))

    projected, summary_text, omitted_count = build_projected_context(
        base_messages=[],
        state_messages=state_messages,
        pinned_message_ids=set(),
        max_recent_messages=6,
    )

    assert omitted_count > 0
    assert summary_text
    assert any(getattr(message, "id", None) == "context_summary_current" for message in projected)
    assert len(projected) <= 7


def test_build_projected_context_keeps_latest_tool_batch():
    state_messages = [HumanMessage(content=f"user-{idx}") for idx in range(12)]
    state_messages.append(
        AIMessage(
            content="running tools",
            tool_calls=[
                {"name": "get_scene_info", "args": {}, "id": "tool-1", "type": "tool_call"},
            ],
        )
    )
    state_messages.append(ToolMessage(name="get_scene_info", content="{}", tool_call_id="tool-1"))
    state_messages.extend(HumanMessage(content=f"later-{idx}") for idx in range(14))

    projected, _, _ = build_projected_context(
        base_messages=[],
        state_messages=state_messages,
        pinned_message_ids=set(),
        max_recent_messages=5,
    )

    assert any(isinstance(message, AIMessage) and getattr(message, "tool_calls", None) for message in projected)
    assert any(isinstance(message, ToolMessage) and message.name == "get_scene_info" for message in projected)
