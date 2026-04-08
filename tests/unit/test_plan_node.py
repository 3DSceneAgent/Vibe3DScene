from langchain_core.messages import HumanMessage

from scene_agent.agent.nodes import plan_node


class _PlannerModelStub:
    def __init__(self, payload):
        self.payload = payload
        self.called = False

    def with_config(self, **_kwargs):
        return self

    def with_structured_output(self, _schema):
        return self

    def invoke(self, _messages):
        self.called = True
        return self.payload


def test_plan_node_creates_ordered_todos_and_sets_active():
    planner = _PlannerModelStub(
        {
            "todos": [
                {"title": "Layout", "description": "Place floor and walls"},
                {"title": "Furniture", "description": "Add sofa and coffee table"},
            ]
        }
    )
    result = plan_node(
        {"messages": [HumanMessage(content="Build a small living room scene with a sofa and lamp.")]},
        planner_model=planner,
    )
    assert result["task_mode"] == "plan_mode"
    assert result["routed_to_plan"] is True
    assert len(result["todos"]) == 2
    assert planner.called is True
    assert result["todos"][0]["description"] == "Layout"
    assert result["todos"][1]["description"] == "Furniture"
    assert isinstance(result["active_todo_id"], str) and result["active_todo_id"]


def test_plan_node_uses_single_fallback_todo_when_model_returns_empty():
    result = plan_node(
        {"messages": [HumanMessage(content="Add one red cube to the scene.")]},
        planner_model=_PlannerModelStub({"todos": []}),
    )
    assert len(result["todos"]) == 1
    assert result["todos"][0]["status"] == "pending"
    assert result["todos"][0]["description"] == "Add one red cube to the scene."


def test_plan_node_fallback_splits_numbered_list_text_into_multiple_todos():
    result = plan_node(
        {"messages": [HumanMessage(content="Help me build a scene.")]},
        planner_model=_PlannerModelStub(
            "1. Add a floor and wall.\n2. Place a sofa near the center.\n3. Add a lamp behind it."
        ),
    )
    assert [todo["description"] for todo in result["todos"]] == [
        "Add a floor and wall.",
        "Place a sofa near the center.",
        "Add a lamp behind it.",
    ]


def test_plan_node_fallback_splits_bullet_list_text_into_multiple_todos() -> None:
    result = plan_node(
        {"messages": [HumanMessage(content="Make a courtyard scene.")]},
        planner_model=_PlannerModelStub(
            "- Add a stone floor.\n- Place a central fountain.\n- Scatter planters around the edges."
        ),
    )
    assert [todo["description"] for todo in result["todos"]] == [
        "Add a stone floor.",
        "Place a central fountain.",
        "Scatter planters around the edges.",
    ]


def test_plan_node_fallback_parses_fenced_json_and_discards_thought() -> None:
    result = plan_node(
        {"messages": [HumanMessage(content="Build the house.")]},
        planner_model=_PlannerModelStub(
            """```json
            [
              {
                "thought": "First I should block out the main form.",
                "description": "Create the main building blocks."
              },
              {
                "thought": "Then I can add facade details.",
                "description": "Insert the large windows and front door."
              }
            ]
            ```"""
        ),
    )
    assert [todo["description"] for todo in result["todos"]] == [
        "Create the main building blocks.",
        "Insert the large windows and front door.",
    ]


def test_plan_node_fallback_uses_request_when_raw_payload_is_not_recoverable() -> None:
    result = plan_node(
        {"messages": [HumanMessage(content="Build a small pavilion.")]},
        planner_model=_PlannerModelStub("```json\n{\"unexpected\": true}\n```"),
    )
    assert len(result["todos"]) == 1
    assert result["todos"][0]["description"] == "Build a small pavilion."


def test_plan_node_early_exit_reuses_existing_todos_without_invoking_model():
    planner = _PlannerModelStub({"todos": [{"title": "Should not run", "description": "Should not run"}]})
    result = plan_node(
        {
            "messages": [HumanMessage(content="Reuse current plan.")],
            "todos": [
                {
                    "id": "todo-1",
                    "description": "Keep existing todo",
                    "status": "pending",
                    "created_at": "2026-01-01T00:00:00",
                    "completed_at": None,
                }
            ],
        },
        planner_model=planner,
    )
    assert planner.called is False
    assert result["task_mode"] == "plan_mode"
    assert result["routed_to_plan"] is True
    assert result["active_todo_id"] == "todo-1"
