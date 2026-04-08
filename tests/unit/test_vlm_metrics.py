from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from scene_agent.vlm import metrics


def _image_message() -> list[HumanMessage]:
    return [
        HumanMessage(
            content=[
                {"type": "text", "text": "describe this scene"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,abc"},
                },
            ]
        )
    ]


def test_resolve_image_input_tokens_uses_gemini_count_delta(monkeypatch):
    messages = _image_message()
    seen_messages: list[list[HumanMessage]] = []

    def fake_count(_model, input_messages):
        seen_messages.append(input_messages)
        if len(seen_messages) == 1:
            return 120
        assert metrics.messages_have_image_inputs(input_messages) is False
        return 45

    monkeypatch.setattr(metrics, "_count_gemini_tokens_from_messages", fake_count)

    image_tokens, has_images = metrics.resolve_image_input_tokens(
        provider_name="gemini",
        model=object(),
        messages=messages,
        image_input_tokens=None,
    )

    assert has_images is True
    assert image_tokens == 75
    assert len(seen_messages) == 2


def test_resolve_image_input_tokens_keeps_qwen_image_breakdown_unknown():
    image_tokens, has_images = metrics.resolve_image_input_tokens(
        provider_name="qwen",
        model=object(),
        messages=_image_message(),
        image_input_tokens=None,
    )

    assert has_images is True
    assert image_tokens is None


def test_extract_usage_fields_from_chat_result_generations():
    response = ChatResult(
        generations=[
            ChatGeneration(
                message=AIMessage(
                    content="telemetry_ok",
                    usage_metadata={
                        "input_tokens": 11,
                        "output_tokens": 7,
                        "total_tokens": 18,
                    },
                )
            )
        ]
    )

    usage = metrics._extract_usage_fields(response)

    assert usage["input_tokens"] == 11
    assert usage["output_tokens"] == 7
    assert usage["total_tokens"] == 18


def test_invoke_with_metrics_falls_back_to_response_usage_when_callback_usage_unknown(monkeypatch):
    response = AIMessage(
        content="telemetry_ok",
        usage_metadata={
            "input_tokens": 9,
            "output_tokens": 4,
            "total_tokens": 13,
        },
    )

    def fake_invoke(_model, _invoke_input, handler):
        handler.usage_fields = {
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "image_input_tokens": None,
        }
        return response

    monkeypatch.setattr(metrics, "_invoke_with_callback", fake_invoke)

    _response, record = metrics.invoke_with_metrics(
        object(),
        [HumanMessage(content="hello")],
        thread_id="thread-1",
        turn_id="turn-1",
        node_name="agent",
        call_role="general",
        provider_name="gemini",
        model_name="gemini-2.5-flash",
    )

    assert record["input_tokens"] == 9
    assert record["output_tokens"] == 4
    assert record["total_tokens"] == 13
