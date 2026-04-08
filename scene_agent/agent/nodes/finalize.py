"""Node implementations by category."""
from typing import Any, Dict
from langchain_core.messages import AIMessage
from scene_agent.agent.state import AgentState

from .shared import (
    build_workflow_metadata,
    compose_finalize_summary,
)


def finalize_node(
    state: AgentState,
    *,
    finalizer_model: Any | None = None,
) -> Dict[str, Any]:
    """
    Finalize node: mark workflow-level finish metadata before END.
    """
    workflow = build_workflow_metadata(state)
    summary, llm_call_records = compose_finalize_summary(
        state,
        workflow,
        finalizer_model=finalizer_model,
    )
    result: Dict[str, Any] = {"workflow": workflow, "messages": [AIMessage(content=summary)]}
    if llm_call_records:
        result["llm_call_records"] = llm_call_records
    return result
