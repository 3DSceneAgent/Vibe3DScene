from langchain_core.messages import HumanMessage

from scene_agent.agent.nodes import initialize_request_node


def test_initialize_request_node_defaults_to_plan_mode():
    result = initialize_request_node(
        {"messages": [HumanMessage(content="What is global illumination?")]}
    )
    assert result["task_mode"] == "plan_mode"
    assert result["task_intent"] == "direct_request"
    assert result["max_request_agent_turns"] > 0
    assert result["max_request_tool_batches"] > 0


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


def test_initialize_request_node_marks_continue_intent_with_unfinished_todos():
    result = initialize_request_node(
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
    assert result["task_intent"] == "continue_existing_plan"
