from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from scene_agent.agent.nodes.shared import ROLE_GENERAL, invoke_role_agent
from scene_agent.agent.nodes.verification import verify_node


class _DummyLLM:
    def __init__(self) -> None:
        self.last_messages = None

    def invoke(self, messages):
        self.last_messages = messages
        return AIMessage(content="ok")


def _todo(todo_id: str, description: str, status: str) -> dict:
    return {
        "id": todo_id,
        "description": description,
        "status": status,
        "created_at": "2026-01-01T00:00:00",
        "completed_at": None,
    }


def test_single_agent_plan_mode_prompt_only_injects_active_todo() -> None:
    llm = _DummyLLM()
    state = {
        "task_mode": "plan_mode",
        "workflow_topology": "single_agent",
        "messages": [HumanMessage(content="Build the beach campsite scene.")],
        "request_tool_batches": 0,
        "max_request_tool_batches": 40,
        "active_todo_id": "todo-1",
        "todos": [
            _todo("todo-1", "Set up the beach environment.", "in_progress"),
            _todo("todo-2", "Add the central camper van.", "pending"),
        ],
    }

    invoke_role_agent(
        state=state,
        llm_with_tools=llm,
        available_tool_names=[],
        role=ROLE_GENERAL,
    )

    system_texts = [
        message.content
        for message in (llm.last_messages or [])
        if isinstance(message, SystemMessage) and isinstance(message.content, str)
    ]
    joined = "\n".join(system_texts)
    assert "Active todo only: todo-1: Set up the beach environment." in joined
    assert "Do not proactively work on later todos" in joined
    assert "Add the central camper van." not in joined

    human_texts = [
        message.content
        for message in (llm.last_messages or [])
        if isinstance(message, HumanMessage) and isinstance(message.content, str)
    ]
    joined_human = "\n".join(human_texts)
    assert "Plan mode execution request:" in joined_human
    assert "Current active todo: todo-1: Set up the beach environment." in joined_human
    assert "Build the beach campsite scene." not in joined_human


def test_single_agent_plan_mode_projects_latest_human_message_but_preserves_images() -> None:
    llm = _DummyLLM()
    state = {
        "task_mode": "plan_mode",
        "workflow_topology": "single_agent",
        "messages": [
            HumanMessage(
                content=[
                    {"type": "text", "text": "Build the beach campsite scene."},
                    {"type": "image_url", "image_url": {"url": "https://example.com/reference.png"}},
                ]
            )
        ],
        "request_tool_batches": 0,
        "max_request_tool_batches": 40,
        "active_todo_id": "todo-1",
        "todos": [
            _todo("todo-1", "Set up the beach environment.", "in_progress"),
            _todo("todo-2", "Add the central camper van.", "pending"),
        ],
    }

    invoke_role_agent(
        state=state,
        llm_with_tools=llm,
        available_tool_names=[],
        role=ROLE_GENERAL,
    )

    human_messages = [message for message in (llm.last_messages or []) if isinstance(message, HumanMessage)]
    projected = human_messages[-1]
    assert isinstance(projected.content, list)
    assert projected.content[0]["type"] == "text"
    assert "Plan mode execution request:" in projected.content[0]["text"]
    assert "Build the beach campsite scene." not in projected.content[0]["text"]
    assert any(
        isinstance(item, dict)
        and item.get("type") == "image_url"
        and item.get("image_url", {}).get("url") == "https://example.com/reference.png"
        for item in projected.content
    )


def test_direct_mode_keeps_existing_full_todo_prompt() -> None:
    llm = _DummyLLM()
    state = {
        "task_mode": "direct_mode",
        "workflow_topology": "single_agent",
        "messages": [HumanMessage(content="Continue from the current scene state.")],
        "request_tool_batches": 0,
        "max_request_tool_batches": 40,
        "active_todo_id": "todo-1",
        "todos": [
            _todo("todo-1", "Set up the beach environment.", "in_progress"),
            _todo("todo-2", "Add the central camper van.", "pending"),
        ],
    }

    invoke_role_agent(
        state=state,
        llm_with_tools=llm,
        available_tool_names=[],
        role=ROLE_GENERAL,
    )

    system_texts = [
        message.content
        for message in (llm.last_messages or [])
        if isinstance(message, SystemMessage) and isinstance(message.content, str)
    ]
    joined = "\n".join(system_texts)
    assert "Current todo state (latest version per todo_id):" in joined
    assert "Set up the beach environment." in joined
    assert "Add the central camper van." in joined
    assert "Do not proactively work on later todos" not in joined


def test_verify_node_limits_todo_context_to_active_todo_in_plan_mode(monkeypatch) -> None:
    captured_kwargs: dict = {}

    def fake_verify_render_with_references(**kwargs):
        captured_kwargs.update(kwargs)
        return {
            "status": "working",
            "reason": "Need to finish the active beach setup.",
            "edit_suggestions": ["Refine the shoreline layout."],
        }

    monkeypatch.setattr(
        "scene_agent.agent.nodes.verification.verify_render_with_references",
        fake_verify_render_with_references,
    )

    result = verify_node(
        {
            "task_mode": "plan_mode",
            "thread_id": "thread-plan-focus",
            "messages": [HumanMessage(content="Build the beach campsite scene.")],
            "last_render_path": "/tmp/plan_focus_render.png",
            "last_verified_path": None,
            "last_render_source": "agent_camera",
            "active_todo_id": "todo-1",
            "todos": [
                _todo("todo-1", "Set up the beach environment.", "in_progress"),
                _todo("todo-2", "Add the central camper van.", "pending"),
            ],
        }
    )

    assert captured_kwargs["todo_context"] == [
        {
            "todo_id": "todo-1",
            "title": "Set up the beach environment.",
            "status": "in_progress",
        }
    ]
    assert captured_kwargs["scene_context"]["active_todos"] == [
        {
            "todo_id": "todo-1",
            "title": "Set up the beach environment.",
            "status": "in_progress",
        }
    ]
    message = result["messages"][0]
    assert isinstance(message, ToolMessage)
    payload = message.content
    assert "central camper van" not in str(payload).lower()


def test_verify_node_keeps_multi_todo_context_outside_plan_mode(monkeypatch) -> None:
    captured_kwargs: dict = {}

    def fake_verify_render_with_references(**kwargs):
        captured_kwargs.update(kwargs)
        return {
            "status": "working",
            "reason": "Need more work.",
            "edit_suggestions": [],
        }

    monkeypatch.setattr(
        "scene_agent.agent.nodes.verification.verify_render_with_references",
        fake_verify_render_with_references,
    )

    verify_node(
        {
            "task_mode": "direct_mode",
            "thread_id": "thread-direct-focus",
            "messages": [HumanMessage(content="Continue refining the campsite.")],
            "last_render_path": "/tmp/direct_focus_render.png",
            "last_verified_path": None,
            "last_render_source": "agent_camera",
            "active_todo_id": "todo-1",
            "todos": [
                _todo("todo-1", "Set up the beach environment.", "in_progress"),
                _todo("todo-2", "Add the central camper van.", "pending"),
            ],
        }
    )

    assert captured_kwargs["todo_context"] == [
        {
            "todo_id": "todo-1",
            "title": "Set up the beach environment.",
            "status": "in_progress",
        },
        {
            "todo_id": "todo-2",
            "title": "Add the central camper van.",
            "status": "pending",
        },
    ]
