import pytest
from google.genai.errors import ServerError
from langchain_core.messages import HumanMessage

from scene_agent.agent.nodes.shared import ROLE_GENERAL, invoke_role_agent


def _base_state() -> dict:
    return {
        "task_mode": "direct_mode",
        "messages": [HumanMessage(content="Place the generated model into the scene.")],
        "request_tool_batches": 0,
        "max_request_tool_batches": 40,
    }


class _MaskedGeminiErrorLLM:
    def invoke(self, _messages):
        try:
            raise ServerError(
                503,
                {
                    "error": {
                        "code": 503,
                        "message": "This model is currently experiencing high demand.",
                        "status": "UNAVAILABLE",
                    }
                },
                None,
            )
        except ServerError:
            raise TypeError("'Response' object is not subscriptable")


class _PlainTypeErrorLLM:
    def invoke(self, _messages):
        raise TypeError("'Response' object is not subscriptable")


def test_invoke_role_agent_reraises_original_google_server_error() -> None:
    with pytest.raises(ServerError, match="503"):
        invoke_role_agent(
            state=_base_state(),
            llm_with_tools=_MaskedGeminiErrorLLM(),
            available_tool_names=[],
            role=ROLE_GENERAL,
        )


def test_invoke_role_agent_keeps_plain_type_error_when_no_provider_error_exists() -> None:
    with pytest.raises(TypeError, match="not subscriptable"):
        invoke_role_agent(
            state=_base_state(),
            llm_with_tools=_PlainTypeErrorLLM(),
            available_tool_names=[],
            role=ROLE_GENERAL,
        )
