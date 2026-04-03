from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from scene_agent.interfaces.api import (
    assistant_message_display_text,
    extract_message_reasoning_text,
    message_has_tool_calls,
    message_is_tool,
    serialize_event,
    serialize_message,
)


def test_serialize_message_passthrough_dict():
    payload = {"type": "ai", "content": "hello"}
    assert serialize_message(payload) == payload


def test_serialize_message_langchain_message():
    message = AIMessage(content="hello", id="msg-1")
    data = serialize_message(message)
    assert data["type"] == message.type
    assert data["content"] == "hello"
    assert data["id"] == "msg-1"


def test_serialize_event_with_messages():
    event = {"messages": [AIMessage(content="hello"), HumanMessage(content="hi")]}
    data = serialize_event(event)
    assert data["messages"][0]["content"] == "hello"
    assert data["messages"][1]["content"] == "hi"


def test_serialize_event_non_dict():
    data = serialize_event("ping")
    assert data == {"event": "ping"}
from dataclasses import dataclass

from scene_agent.interfaces.api import serialize_event


@dataclass
class DummyMessage:
    type: str
    content: str


def test_serialize_event_handles_messages() -> None:
    message = DummyMessage(type="ai", content="hello")
    payload = serialize_event({"messages": [message]})
    assert "messages" in payload
    assert payload["messages"][0]["type"] == "ai"
    assert payload["messages"][0]["content"] == "hello"


def test_serialize_event_wraps_non_dict() -> None:
    payload = serialize_event("event")
    assert payload == {"event": "event"}


def test_message_is_tool() -> None:
    message = ToolMessage(content="ok", name="blender", tool_call_id="tool-1")
    data = serialize_message(message)
    assert data["tool_call_id"] == "tool-1"
    assert message_is_tool(data) is True


def test_message_has_tool_calls() -> None:
    message = AIMessage(content="hi", additional_kwargs={"tool_calls": [{"id": "tool-1"}]})
    data = serialize_message(message)
    assert message_has_tool_calls(data) is True


def test_message_has_tool_calls_from_top_level_field() -> None:
    message = AIMessage(
        content="hi",
        tool_calls=[{"name": "get_scene_info", "args": {}, "id": "tool-1", "type": "tool_call"}],
    )
    data = serialize_message(message)
    assert isinstance(data.get("tool_calls"), list)
    assert message_has_tool_calls(data) is True


def test_assistant_message_display_text_skips_tool_use_json_noise() -> None:
    message = AIMessage(
        content=[
            {"type": "text", "text": "Inspecting scene before edit."},
            {"type": "tool_use", "name": "get_scene_info", "input": {}, "id": "tool-1"},
        ],
        tool_calls=[{"name": "get_scene_info", "args": {}, "id": "tool-1", "type": "tool_call"}],
    )
    data = serialize_message(message)
    assert assistant_message_display_text(data) == "Inspecting scene before edit."


def test_assistant_message_display_text_returns_empty_when_content_empty() -> None:
    message = AIMessage(
        content="",
        tool_calls=[
            {"name": "clear_scene", "args": {}, "id": "tool-1", "type": "tool_call"},
            {"name": "execute_blender_code", "args": {}, "id": "tool-2", "type": "tool_call"},
        ],
    )
    data = serialize_message(message)
    assert assistant_message_display_text(data) == ""


def test_extract_message_reasoning_text_from_qwen_reasoning_content() -> None:
    message = AIMessageChunk(
        id="assistant-qwen",
        content="",
        additional_kwargs={"reasoning_content": "Need to inspect the scene first."},
    )
    data = serialize_message(message)
    assert extract_message_reasoning_text(data) == "Need to inspect the scene first."


def test_extract_message_reasoning_text_from_gemini_thinking_block() -> None:
    message = AIMessage(
        content=[
            {"type": "thinking", "thinking": "Compare layout before calling the tool.", "signature": "abc"},
            {"type": "text", "text": "", "extras": {"signature": "abc"}},
        ],
    )
    data = serialize_message(message)
    assert extract_message_reasoning_text(data) == "Compare layout before calling the tool."
