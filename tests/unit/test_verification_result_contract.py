from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from scene_agent.agent.nodes import (
    VerificationResult as NodeVerificationResult,
    evaluator_node,
    verifier_feedback_node,
    verify_node,
)
from scene_agent.verification_result import (
    VerificationResult as CanonicalVerificationResult,
    normalize_verification_payload,
)
from scene_agent.vlm.verification import (
    VerificationResult as VlmVerificationResult,
    verify_render_with_references,
)


def test_verify_node_writes_canonical_verification_result(monkeypatch):
    monkeypatch.setattr(
        "scene_agent.agent.nodes.verification.verify_render_with_references",
        lambda **_kwargs: {
            "status": "done",
            "reason": "Objective satisfied.",
            "edit_suggestions": [],
        },
    )
    result = verify_node(
        {
            "messages": [HumanMessage(content="Place a cube.")],
            "last_render_path": "/tmp/render.png",
            "last_verified_path": None,
            "last_render_source": "agent_camera",
        }
    )
    assert result["verification_result"]["status"] == "done"


def test_verifier_feedback_writes_verification_result_when_verifier_stops():
    result = verifier_feedback_node(
        {
            "messages": [
                AIMessage(
                    content='{"status":"working","reason":"Need to move lamp.","edit_suggestions":["Move lamp left"]}'
                )
            ]
        }
    )
    payload = result["verification_result"]
    assert payload["status"] == "working"
    assert payload["reason"] == "Need to move lamp."


def test_evaluator_consumes_verification_result_contract():
    result = evaluator_node(
        {
            "routed_to_plan": False,
            "request_tool_batches": 1,
            "verification_result": {
                "status": "done",
                "reason": "Completed",
                "edit_suggestions": [],
            },
        }
    )
    assert result["transition_next"] == "finalize"


def test_verification_result_is_shared_contract():
    assert NodeVerificationResult is CanonicalVerificationResult
    assert VlmVerificationResult is CanonicalVerificationResult


def test_verifier_feedback_heuristic_keeps_negative_completion_text_working():
    result = verifier_feedback_node(
        {
            "messages": [
                AIMessage(
                    content="The requested layout is not done yet. The scene is still incorrect and not completed."
                )
            ]
        }
    )
    payload = result["verification_result"]
    assert payload["status"] == "working"


def test_normalize_verification_payload_sanitizes_edit_suggestions():
    payload = normalize_verification_payload(
        {
            "status": "completed",
            "reason": "All checks passed.",
            "edit_suggestions": ["  Move lamp left  ", 123],
        }
    )
    assert payload == {
        "status": "done",
        "reason": "All checks passed.",
        "edit_suggestions": ["Move lamp left"],
    }


def test_verify_render_with_references_logs_structured_output_fallback(monkeypatch):
    events: list[tuple[str, str, dict | None]] = []

    class _FallbackChatModel:
        def with_config(self, **_kwargs):
            return self

        def with_structured_output(self, _schema):
            raise RuntimeError("structured output unsupported")

        def invoke(self, _messages):
            return SimpleNamespace(content='{"status":"working","reason":"Need more work."}')

    class _Provider:
        def get_chat_model(self):
            return _FallbackChatModel()

    monkeypatch.setattr(
        "scene_agent.vlm.verification.get_settings",
        lambda: SimpleNamespace(
            vlm_provider="openai",
            get_vlm_default_model=lambda _provider: "test-model",
            get_vlm_api_key=lambda _provider: "test-key",
        ),
    )
    monkeypatch.setattr(
        "scene_agent.vlm.verification.get_vlm_provider",
        lambda **_kwargs: _Provider(),
    )
    monkeypatch.setattr(
        "scene_agent.vlm.verification._image_to_data_url",
        lambda path: f"data:image/png;base64,{path}",
    )
    monkeypatch.setattr(
        "scene_agent.vlm.verification.log_event",
        lambda level, message, context=None: events.append((level, message, context)),
    )

    result = verify_render_with_references(
        render_path="/tmp/render.png",
        reference_paths=[],
        user_request="Place a chair next to the table.",
        provider_name="openai",
        api_key="test-key",
        model="test-model",
    )

    assert result["structured_output_fallback"] is True
    assert result["status"] == "working"
    assert len(events) == 1
    assert events[0][0] == "warning"
    assert events[0][1] == "verification_structured_output_fallback"
    assert events[0][2]["error_type"] == "RuntimeError"
