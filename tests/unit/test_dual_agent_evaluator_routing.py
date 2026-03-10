from scene_agent.agent import graph as graph_module


def test_dual_post_builder_no_calls_routes_to_evaluator():
    next_node = graph_module._route_after_post_builder({"assistant_turn_kind": "no_calls"})
    assert next_node == "evaluator"


def test_dual_post_builder_has_calls_routes_to_tools():
    next_node = graph_module._route_after_post_builder({"assistant_turn_kind": "has_calls"})
    assert next_node == "tools"


def test_dual_verifier_feedback_has_camera_calls_routes_to_tools():
    next_node = graph_module._route_after_verifier_feedback({"assistant_turn_kind": "has_calls"})
    assert next_node == "tools"


def test_dual_verifier_feedback_no_calls_routes_to_evaluator():
    next_node = graph_module._route_after_verifier_feedback({"assistant_turn_kind": "no_calls"})
    assert next_node == "evaluator"


def test_dual_update_memory_routes_back_to_verifier_agent():
    next_node = graph_module._route_after_update_memory({"workflow_topology": "dual_agent"})
    assert next_node == "verifier_agent"
