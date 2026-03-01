"""Internal non-Blender tools exposed to the agent."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool

from scene_agent.agent.todo_protocol import TODO_UPDATE_TOOL_NAME, TodoUpdateRequest


def _todo_update_tool(actions: list[dict[str, Any]]) -> dict[str, Any]:
    """Record structured todo updates for the current request turn."""
    return {
        "accepted": True,
        "action_count": len(actions),
    }


def get_internal_agent_tools() -> list[Any]:
    todo_tool = StructuredTool.from_function(
        func=_todo_update_tool,
        name=TODO_UPDATE_TOOL_NAME,
        description=(
            "Commit structured todo changes for complex multi-step scene tasks. "
            "Use this instead of emitting textual todo lists."
        ),
        args_schema=TodoUpdateRequest,
    )
    return [todo_tool]
