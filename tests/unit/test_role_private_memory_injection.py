from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from scene_agent.agent.nodes.shared import ROLE_BUILDER, invoke_role_agent


class _DummyLLM:
    def __init__(self) -> None:
        self.last_messages = None

    def invoke(self, messages):
        self.last_messages = messages
        return AIMessage(content="ok")


def _base_state() -> dict:
    return {
        "task_mode": "plan_mode",
        "messages": [HumanMessage(content="Build a reading corner scene.")],
        "request_tool_batches": 0,
        "max_request_tool_batches": 40,
    }


def test_invoke_role_agent_injects_role_private_memory_for_builder():
    llm = _DummyLLM()
    state = _base_state()
    state["memory_profile"] = "shared_plus_role_private"
    state["role_private_memory"] = {
        "builder": {
            "last_action_summary": "Moved chair 0.5m left.",
            "replan_count": 2,
        }
    }

    invoke_role_agent(
        state=state,
        llm_with_tools=llm,
        available_tool_names=[],
        role=ROLE_BUILDER,
    )

    system_texts = [
        message.content
        for message in (llm.last_messages or [])
        if isinstance(message, SystemMessage)
    ]
    assert any(
        isinstance(text, str) and "Role-private memory" in text and "last action summary" in text
        for text in system_texts
    )


def test_invoke_role_agent_skips_role_private_memory_when_profile_not_enabled():
    llm = _DummyLLM()
    state = _base_state()
    state["memory_profile"] = "thread_shared_only"
    state["role_private_memory"] = {
        "builder": {
            "last_action_summary": "Moved chair 0.5m left.",
        }
    }

    invoke_role_agent(
        state=state,
        llm_with_tools=llm,
        available_tool_names=[],
        role=ROLE_BUILDER,
    )

    system_texts = [
        message.content
        for message in (llm.last_messages or [])
        if isinstance(message, SystemMessage)
    ]
    assert not any(isinstance(text, str) and "Role-private memory" in text for text in system_texts)
