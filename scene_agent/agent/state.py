"""
Agent state definitions with LangGraph best practices.
Uses TypedDict with Annotated reducers for proper state management.
"""
from typing import Any, Literal, NotRequired, Sequence, TypedDict, Annotated
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from datetime import datetime
from uuid import uuid4


class TodoItem(TypedDict):
    """Individual todo item for task tracking"""
    id: str
    description: str
    status: str  # "pending" | "in_progress" | "completed" | "failed"
    created_at: str
    completed_at: str | None


TaskMode = Literal["conversation_mode", "single_action_mode", "plan_mode"]


def merge_todos(existing: list[TodoItem], new: list[TodoItem]) -> list[TodoItem]:
    """
    Merge todos by id, updating existing ones with same id.
    
    Args:
        existing: Current list of todos
        new: New todos to merge (can update existing by id)
        
    Returns:
        Merged list of todos
    """
    todo_dict = {todo["id"]: todo for todo in existing}
    for todo in new:
        todo_dict[todo["id"]] = todo
    return list(todo_dict.values())


def replace_mapping(existing: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Replace mapping state with the latest payload."""
    _ = existing
    if not isinstance(new, dict):
        return {}
    return dict(new)


def merge_unique_strings(existing: list[str], new: list[str]) -> list[str]:
    """
    Merge string lists with stable order and de-duplication.
    """
    merged: list[str] = []
    seen: set[str] = set()
    for source in (existing, new):
        if not isinstance(source, list):
            continue
        for item in source:
            if not isinstance(item, str):
                continue
            value = item.strip()
            if not value or value in seen:
                continue
            seen.add(value)
            merged.append(value)
    return merged


class AgentState(TypedDict):
    """
    Agent state with proper reducers following LangGraph best practices.
    
    Fields:
        messages: Conversation history with automatic message accumulation
        scene_objects: Dict of objects in the scene {name: {position, size, type, bbox}}
        persistent_cameras: List of camera names seen in scene-level observe
        todos: List of todo items for task tracking
        thread_id: Conversation/session identifier
        enabled_tool_names: Optional runtime MCP tool allow-list for this request
        task_id: Optional task identifier for image-role bindings
        last_render_path: Latest render file path from tools
        last_verified_path: Latest render path verified by VLM
        tool_round_count: Total tool batches executed for this thread graph state
        request_tool_batches: Tool batches executed in this request run
        request_agent_turns: Agent turns executed in this request run
        max_request_tool_batches: Request-level tool batch budget
        max_request_agent_turns: Request-level agent turn budget
        request_stop_reason: Budget/control stop reason for this request
        last_tool_batch_names: Tool names observed in latest tool batch
        task_mode: Routed workflow mode for this request
        task_intent: Routed intent label for this request
        todo_check_gate: Runtime gate decision for whether to run todo_check
        todo_check: Latest todo_check result payload
        last_todo_check_round: Tool round index when todo_check last ran
        last_todo_snapshot: Last status snapshot used for stagnation detection
        stagnation_count: Consecutive todo_check rounds without todo status change
        verify_forced_recovery: Whether verify node forced hard-recovery tool calls
        catastrophic_recovery_attempts: Consecutive catastrophic hard-recovery attempts
        workflow: Final workflow metadata from finalize node
    """
    # Messages with built-in reducer for proper message accumulation
    messages: Annotated[Sequence[BaseMessage], add_messages]

    # Session identifier
    thread_id: str
    enabled_tool_names: NotRequired[list[str] | None]
    task_id: NotRequired[str | None]
    task_mode: NotRequired[TaskMode]
    task_intent: NotRequired[str]
    tool_policy: NotRequired[str]

    # Verification tracking
    last_render_path: NotRequired[str | None]
    last_verified_path: NotRequired[str | None]
    tool_round_count: NotRequired[int]
    request_tool_batches: NotRequired[int]
    request_agent_turns: NotRequired[int]
    max_request_tool_batches: NotRequired[int]
    max_request_agent_turns: NotRequired[int]
    request_stop_reason: NotRequired[str | None]
    last_tool_batch_names: NotRequired[list[str]]

    # State collections
    scene_objects: NotRequired[Annotated[dict[str, Any], replace_mapping]]
    persistent_cameras: NotRequired[Annotated[list[str], merge_unique_strings]]
    scene_camera_params: NotRequired[Annotated[dict[str, Any], replace_mapping]]
    todos: NotRequired[Annotated[list[TodoItem], merge_todos]]

    # Workflow checkpoints
    todo_check_gate: NotRequired[dict]
    todo_check: NotRequired[dict]
    last_todo_check_round: NotRequired[int]
    last_todo_check_verified_path: NotRequired[str | None]
    last_todo_snapshot: NotRequired[dict[str, str]]
    stagnation_count: NotRequired[int]
    verify_forced_recovery: NotRequired[bool]
    catastrophic_recovery_attempts: NotRequired[int]
    # Scene-level camera state — updated by scene_observe_node
    scene_bbox: NotRequired[dict]
    # {"center": [x,y,z], "dimensions": [w,h,d]} — union AABB of all mesh objects

    last_render_source: NotRequired[str]
    # "scene_observe" | "agent_camera" — helps verify pick the right prompt

    workflow: NotRequired[dict[str, Any]]


def create_todo(description: str, status: str = "pending") -> TodoItem:
    """
    Create a new todo item.
    
    Args:
        description: Description of the todo
        status: Status of the todo (default: pending)
        
    Returns:
        New TodoItem
    """
    completed_at = datetime.now().isoformat() if status == "completed" else None
    return TodoItem(
        id=f"todo_{uuid4().hex}",
        description=description,
        status=status,
        created_at=datetime.now().isoformat(),
        completed_at=completed_at
    )
