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


class ReferenceImageCatalogEntry(TypedDict):
    """Compact request-scoped reference image catalog entry."""
    asset_id: str
    stored_path: str
    caption: str
    source_turn_at: str
    created_at: str
    last_used_at: str | None
    use_count: int


TaskMode = Literal["conversation_mode", "single_action_mode", "plan_mode"]
WorkflowTopology = Literal["single_agent", "dual_agent"]
MemoryProfile = Literal["thread_shared_only", "shared_plus_role_private"]
AgentRole = Literal["general", "builder", "verifier"]


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
        attached_image_ids: Optional uploaded image IDs explicitly attached to this user request
        task_mode: Routed workflow mode for this request
        task_intent: Routed intent label for this request
        router_decision: Latest structured routing decision payload from LLM router
        router_confidence: Router confidence score [0, 1]
        router_need_clarification: Whether router requires user clarification before execution
        router_clarification_question: Router-proposed clarification question
        workflow_topology_request: Optional request-level topology hint
        memory_profile_request: Optional request-level memory-profile hint
        workflow_topology: Effective workflow topology for this request
        memory_profile: Effective memory profile for this request
        active_role: Active role in current request (`general/builder/verifier`)
        builder_turn_count: Builder turns executed in this request run
        verifier_turn_count: Verifier turns executed in this request run
        builder_stall_count: Consecutive builder turns without tool calls
        verifier_feedback: Latest structured verifier feedback
        role_private_memory: Compact role-scoped private memory buckets
        reference_image_catalog: Compact named reference-image catalog for this thread state
        request_reference_image_keys: Active reference-image keys for the current request
        request_reference_image_source: Source label for the current request reference-image set
        request_reference_image_reason: Human-readable reason for the current request reference-image set
        plan_replan_count: Number of plan refreshes in this request run
        max_plan_replans: Max allowed plan refresh attempts in this request run
        verification_mismatch_streak: Consecutive mismatch/catastrophic verification count
        quality_eval: Latest quality evaluator output
        progress_eval: Latest progress evaluator output
        budget_eval: Latest budget evaluator output
        transition_next: Cached deterministic transition decision
        transition_reason: Human-readable transition reason for observability
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
    attached_image_ids: NotRequired[list[str] | None]
    task_mode: NotRequired[TaskMode]
    task_intent: NotRequired[str]
    router_decision: NotRequired[dict[str, Any]]
    router_confidence: NotRequired[float]
    router_need_clarification: NotRequired[bool]
    router_clarification_question: NotRequired[str]
    tool_policy: NotRequired[str]
    workflow_topology_request: NotRequired[str | None]
    memory_profile_request: NotRequired[str | None]
    workflow_topology: NotRequired[WorkflowTopology]
    memory_profile: NotRequired[MemoryProfile]
    active_role: NotRequired[AgentRole]
    transition_next: NotRequired[str]

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
    builder_turn_count: NotRequired[int]
    verifier_turn_count: NotRequired[int]
    builder_stall_count: NotRequired[int]
    verifier_feedback: NotRequired[dict[str, Any]]
    role_private_memory: NotRequired[dict[str, dict[str, Any]]]
    reference_image_catalog: NotRequired[Annotated[dict[str, ReferenceImageCatalogEntry], replace_mapping]]
    request_reference_image_keys: NotRequired[list[str]]
    request_reference_image_source: NotRequired[str]
    request_reference_image_reason: NotRequired[str | None]
    plan_replan_count: NotRequired[int]
    max_plan_replans: NotRequired[int]
    verification_mismatch_streak: NotRequired[int]
    quality_eval: NotRequired[dict[str, Any]]
    progress_eval: NotRequired[dict[str, Any]]
    budget_eval: NotRequired[dict[str, Any]]
    transition_reason: NotRequired[str]

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
