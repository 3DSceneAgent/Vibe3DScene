"""
Agent state definitions with LangGraph best practices.
Uses TypedDict with Annotated reducers for proper state management.
"""
from typing import TypedDict, Annotated, Sequence, NotRequired
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from operator import add
from datetime import datetime


class TodoItem(TypedDict):
    """Individual todo item for task tracking"""
    id: str
    description: str
    status: str  # "pending" | "in_progress" | "completed" | "failed"
    created_at: str
    completed_at: str | None


class ReferenceImageInfo(TypedDict):
    id: str
    thread_id: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    stored_path: str
    uploaded_at: str


class DiagnosticInfo(TypedDict):
    request_id: str
    thread_id: str
    session_id: str | None
    process_id: int | None
    log_path: str | None
    elapsed_ms: int
    status: str


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


def merge_reference_images(
    existing: list[ReferenceImageInfo],
    new: list[ReferenceImageInfo]
) -> list[ReferenceImageInfo]:
    image_map = {image["id"]: image for image in existing}
    for image in new:
        image_map[image["id"]] = image
    return list(image_map.values())


def merge_dicts(existing: dict, new: dict) -> dict:
    """Merge two dictionaries, with new values overwriting existing ones"""
    return {**existing, **new}


class AgentState(TypedDict):
    """
    Agent state with proper reducers following LangGraph best practices.
    
    Fields:
        messages: Conversation history with automatic message accumulation
        scene_objects: Dict of objects in the scene {name: {position, size, type, bbox}}
        persistent_cameras: List of camera names that should be tracked
        camera_renderings: Dict of {camera_name: [rendering_data]}
        todos: List of todo items for task tracking
        reference_images: List of reference image metadata for verification
        diagnostics: Diagnostic metadata keyed by request id
        thread_id: Conversation/session identifier
        enabled_tool_names: Optional runtime MCP tool allow-list for this request
        last_render_path: Latest render file path from tools
        last_verified_path: Latest render path verified by VLM
        last_render_signature: Signature for last render sent to VLM
        tool_round_count: Number of tool batches executed in current request loop
        last_tool_batch_names: Tool names observed in latest tool batch
        agent_decision: Structured decision payload from agent responses
        todo_check_gate: Runtime gate decision for whether to run todo_check
        todo_check: Latest todo_check result payload
        last_todo_check_round: Tool round index when todo_check last ran
        last_todo_snapshot: Last status snapshot used for stagnation detection
        stagnation_count: Consecutive todo_check rounds without todo status change
        current_task: Description of current user request
        iteration_count: Number of agent iterations
        last_error: Last error message if any
    """
    # Messages with built-in reducer for proper message accumulation
    messages: Annotated[Sequence[BaseMessage], add_messages]
    
    # Scene objects - merges dicts, allowing incremental updates
    scene_objects: Annotated[dict, merge_dicts]
    
    # Persistent cameras - uses add operator for list accumulation
    persistent_cameras: Annotated[list[str], add]
    
    # Camera renderings - merge strategy for dict updates
    camera_renderings: Annotated[dict, merge_dicts]
    
    # Todo tracking - merge by id for task management
    todos: Annotated[list[TodoItem], merge_todos]

    # Reference images - merge by id
    reference_images: Annotated[list[ReferenceImageInfo], merge_reference_images]

    # Diagnostics - merge by key
    diagnostics: Annotated[dict, merge_dicts]

    # Session identifier
    thread_id: str
    enabled_tool_names: NotRequired[list[str] | None]

    # Verification tracking
    last_render_path: str | None
    last_verified_path: str | None
    last_render_signature: str | None
    tool_round_count: NotRequired[int]
    last_tool_batch_names: NotRequired[list[str]]
    agent_decision: dict
    todo_check_gate: NotRequired[dict]
    todo_check: NotRequired[dict]
    last_todo_check_round: NotRequired[int]
    last_todo_snapshot: NotRequired[dict[str, str]]
    stagnation_count: NotRequired[int]
    
    # Scene-level camera state — updated by scene_observe_node
    scene_camera_params: Annotated[dict, merge_dicts]
    # {"SceneCamera_NE": {"location": [...], "focal_mm": 50.0, "azimuth": 45}, ...}

    last_scene_observe_round: NotRequired[int]
    # Tool round when scene_observe last rendered

    scene_bbox: NotRequired[dict]
    # {"center": [x,y,z], "dimensions": [w,h,d]} — union AABB of all mesh objects

    last_render_source: NotRequired[str]
    # "scene_observe" | "agent_camera" — helps verify pick the right prompt

    # Simple fields (last write wins)
    current_task: str
    iteration_count: int
    last_error: str | None


def create_todo(description: str, status: str = "pending") -> TodoItem:
    """
    Create a new todo item.
    
    Args:
        description: Description of the todo
        status: Status of the todo (default: pending)
        
    Returns:
        New TodoItem
    """
    return TodoItem(
        id=f"todo_{datetime.now().timestamp()}",
        description=description,
        status=status,
        created_at=datetime.now().isoformat(),
        completed_at=None
    )


def update_todo_status(todo: TodoItem, status: str) -> TodoItem:
    """
    Update the status of a todo item.
    
    Args:
        todo: The todo to update
        status: New status
        
    Returns:
        Updated TodoItem
    """
    updated = dict(todo)
    updated["status"] = status
    if status == "completed":
        updated["completed_at"] = datetime.now().isoformat()
    return TodoItem(**updated)
