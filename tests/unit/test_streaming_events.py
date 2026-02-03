from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from scene_agent.interfaces.api import (
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
    assert message_is_tool(data) is True


def test_message_has_tool_calls() -> None:
    message = AIMessage(content="hi", additional_kwargs={"tool_calls": [{"id": "tool-1"}]})
    data = serialize_message(message)
    assert message_has_tool_calls(data) is True
