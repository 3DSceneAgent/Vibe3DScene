from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from scene_agent.agent.context_manager import build_projected_context


class FakeTokenCounter:
    profile = {"max_input_tokens": 60}

    def get_num_tokens_from_messages(self, messages):
        total = 0
        for message in messages:
            content = getattr(message, "content", "")
            if isinstance(content, str):
                total += len(content)
            else:
                total += 10
        return total


class FakeSummaryModel:
    def with_config(self, **_kwargs):
        return self

    def invoke(self, _messages, config=None):
        _ = config
        return AIMessage(content="- Summarized prior work.\n- Kept the active constraint.")


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


def test_build_projected_context_can_trim_by_token_budget():
    state_messages = [
        HumanMessage(content="x" * 18),
        HumanMessage(content="y" * 18),
        HumanMessage(content="z" * 18),
    ]

    projected, summary_text, omitted_count = build_projected_context(
        base_messages=[],
        state_messages=state_messages,
        pinned_message_ids=set(),
        max_recent_messages=12,
        token_counter=FakeTokenCounter(),
    )

    assert omitted_count >= 1
    assert len(projected) <= 2
    assert getattr(projected[-1], "content", "") == "z" * 18
    assert isinstance(summary_text, str)


def test_build_projected_context_uses_summary_model_when_available():
    state_messages = [HumanMessage(content=f"user-{idx}") for idx in range(9)]

    projected, summary_text, omitted_count = build_projected_context(
        base_messages=[],
        state_messages=state_messages,
        pinned_message_ids=set(),
        max_recent_messages=4,
        summary_model=FakeSummaryModel(),
    )

    assert omitted_count == 5
    assert "Summarized prior work." in summary_text
    assert any(getattr(message, "id", None) == "context_summary_current" for message in projected)
