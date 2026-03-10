from langchain_core.messages import HumanMessage

from scene_agent.agent.nodes import router_node


class _FailingRouterModel:
    def with_config(self, **_kwargs):
        return self

    def with_structured_output(self, _schema):
        return self

    def invoke(self, _messages):
        raise RuntimeError("provider error")


def test_router_structured_output_defaults_to_plan_mode_on_error():
    result = router_node(
        {
            "messages": [HumanMessage(content="Please recreate the full scene from this reference image.")],
            "attached_image_ids": ["asset-1"],
        },
        router_model=_FailingRouterModel(),
    )
    assert result["routed_to_plan"] is True
    assert result["task_mode"] == "plan_mode"
    assert result["router_decision"]["reasoning"] == "router_model_error_default_plan_mode"
