from langchain_core.messages import HumanMessage

from scene_agent.agent.nodes import initialize_request_node, router_node


class _RouterModelStub:
    def __init__(self, payload):
        self.payload = payload
        self.configs: list[dict] = []

    def with_config(self, **kwargs):
        self.configs.append(kwargs)
        return self

    def with_structured_output(self, _schema):
        return self

    def invoke(self, _messages):
        return self.payload


def test_initialize_request_node_defaults_to_direct_mode():
    result = initialize_request_node(
        {"messages": [HumanMessage(content="What is global illumination?")]}
    )
    assert result["task_mode"] == "direct_mode"
    assert result["task_intent"] == "direct_request"
    assert result["routed_to_plan"] is False


def test_initialize_request_node_resolves_dual_topology_request():
    result = initialize_request_node(
        {
            "messages": [HumanMessage(content="Build a full scene.")],
            "workflow_topology_request": "dual_agent",
        }
    )
    assert result["workflow_topology"] == "dual_agent"
    assert result["active_role"] == "builder"
    assert result["memory_profile"] == "shared_plus_role_private"


def test_router_node_forces_plan_when_unfinished_todos_exist():
    result = router_node(
        {
            "messages": [HumanMessage(content="continue")],
            "todos": [
                {
                    "id": "todo-1",
                    "description": "Arrange layout",
                    "status": "in_progress",
                    "created_at": "2026-01-01T00:00:00",
                    "completed_at": None,
                }
            ],
        }
    )
    assert result["routed_to_plan"] is True
    assert result["task_mode"] == "plan_mode"
    assert result["task_intent"] == "continue_existing_plan"


def test_router_node_uses_structured_output_when_model_available():
    model = _RouterModelStub({"needs_plan": False, "reasoning": "simple_qa"})
    result = router_node(
        {"messages": [HumanMessage(content="How do shadows work?")]},
        router_model=model,
    )
    assert result["routed_to_plan"] is False
    assert result["task_mode"] == "direct_mode"
    assert any(cfg.get("run_name") == "router_internal" for cfg in model.configs)


def test_router_node_defaults_to_plan_mode_without_model():
    result = router_node(
        {"messages": [HumanMessage(content="How do shadows work?")]},
        router_model=None,
    )
    assert result["routed_to_plan"] is True
    assert result["task_mode"] == "plan_mode"
    assert result["router_decision"]["reasoning"] == "router_model_unavailable_default_plan_mode"
