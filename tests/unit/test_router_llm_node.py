from langchain_core.messages import AIMessage, HumanMessage

from scene_agent.agent.nodes import clarification_node, route_mode_node


class _StubStructuredRouter:
    def __init__(self, payload: dict):
        self._payload = payload

    def invoke(self, _messages):
        return self._payload


class _StubRouterModel:
    def __init__(self, payload: dict):
        self._payload = payload

    def with_config(self, **_kwargs):
        return self

    def with_structured_output(self, _schema):
        return _StubStructuredRouter(self._payload)


class _FailingRouterModel:
    def with_config(self, **_kwargs):
        return self

    def with_structured_output(self, _schema):
        class _Broken:
            def invoke(self, _messages):
                raise RuntimeError("router unavailable")

        return _Broken()


def test_route_mode_node_routes_conversation_from_llm_router():
    result = route_mode_node(
        {"messages": [HumanMessage(content="What is global illumination?")]},
        router_model=_StubRouterModel(
            {
                "intent": "qa",
                "mode": "conversation_mode",
                "confidence": 0.91,
                "need_clarification": False,
                "clarification_question": "",
                "requires_scene_mutation": False,
            }
        ),
    )

    assert result["task_mode"] == "conversation_mode"
    assert result["router_need_clarification"] is False


def test_route_mode_node_routes_single_action_from_llm_router():
    result = route_mode_node(
        {"messages": [HumanMessage(content="Add one wooden chair.")]},
        router_model=_StubRouterModel(
            {
                "intent": "single_scene_action",
                "mode": "single_action_mode",
                "confidence": 0.88,
                "need_clarification": False,
                "clarification_question": "",
                "requires_scene_mutation": True,
            }
        ),
    )

    assert result["task_mode"] == "single_action_mode"
    assert result["router_need_clarification"] is False


def test_route_mode_node_routes_plan_from_llm_router():
    result = route_mode_node(
        {
            "messages": [HumanMessage(content="Rebuild the full scene from references.")],
            "workflow_topology_request": "dual_agent",
        },
        router_model=_StubRouterModel(
            {
                "intent": "scene_reconstruction",
                "mode": "plan_mode",
                "confidence": 0.94,
                "need_clarification": False,
                "clarification_question": "",
                "requires_scene_mutation": True,
            }
        ),
    )

    assert result["task_mode"] == "plan_mode"
    assert result["workflow_topology"] == "dual_agent"


def test_route_mode_node_requires_clarification_on_low_confidence():
    result = route_mode_node(
        {"messages": [HumanMessage(content="Make it better.")]},
        router_model=_StubRouterModel(
            {
                "intent": "single_scene_action",
                "mode": "single_action_mode",
                "confidence": 0.42,
                "need_clarification": False,
                "clarification_question": "",
                "requires_scene_mutation": True,
            }
        ),
    )

    assert result["router_need_clarification"] is True
    assert isinstance(result["router_clarification_question"], str)
    assert result["router_clarification_question"].strip()


def test_route_mode_node_requires_clarification_on_router_failure():
    result = route_mode_node(
        {"messages": [HumanMessage(content="Do something with this.")]},
        router_model=_FailingRouterModel(),
    )

    assert result["router_need_clarification"] is True
    clarification = clarification_node(result)
    assert isinstance(clarification["messages"][0], AIMessage)
    assert clarification["messages"][0].content.strip()
