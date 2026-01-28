"""
Agent state definitions with LangGraph best practices.
Uses TypedDict with Annotated reducers for proper state management.
"""
from typing import TypedDict, Annotated, Sequence
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
