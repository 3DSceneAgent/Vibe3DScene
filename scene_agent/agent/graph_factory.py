"""
Graph assembly helpers for workflow topology composition.
"""
from __future__ import annotations

from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from scene_agent.agent.state import AgentState


def build_agent_state_graph(
    *,
    initialize_request_node: Callable[..., Any],
    sync_reference_catalog_node: Callable[..., Any],
    prepare_reference_context_node: Callable[..., Any],
    agent_node: Callable[..., Any],
    turn_dispatch_node: Callable[..., Any],
    builder_agent_node: Callable[..., Any],
    post_builder_node: Callable[..., Any],
    verifier_camera_agent_node: Callable[..., Any],
    post_verifier_node: Callable[..., Any],
    verifier_feedback_node: Callable[..., Any],
    quality_evaluator_node: Callable[..., Any],
    progress_evaluator_node: Callable[..., Any],
    budget_evaluator_node: Callable[..., Any],
    tools_node: Any,
    todo_commit_node: Callable[..., Any],
    update_memory_node: Callable[..., Any],
    scene_observe_node: Callable[..., Any],
    checkpoint_finalize_node: Callable[..., Any],
    verify_node: Callable[..., Any],
    transition_resolver_node: Callable[..., Any],
    planner_refresh_node: Callable[..., Any],
    finalize_node: Callable[..., Any],
    route_after_initialize_request: Callable[..., Any],
    route_after_sync_reference_catalog: Callable[..., Any],
    route_after_prepare_reference_context: Callable[..., Any],
    route_after_turn_dispatch: Callable[..., Any],
    route_after_todo_commit: Callable[..., Any],
    route_after_post_builder: Callable[..., Any],
    route_after_post_verifier: Callable[..., Any],
    route_after_verify: Callable[..., Any],
    route_after_transition_resolver: Callable[..., Any],
    route_after_finalize_checkpoint: Callable[..., Any],
) -> StateGraph:
    builder = StateGraph(AgentState)

    builder.add_node("initialize_request", initialize_request_node)
    builder.add_node("sync_reference_catalog", sync_reference_catalog_node)
    builder.add_node("prepare_reference_context", prepare_reference_context_node)
    builder.add_node("agent", agent_node)
    builder.add_node("turn_dispatch", turn_dispatch_node)
    builder.add_node("builder_agent", builder_agent_node)
    builder.add_node("post_builder", post_builder_node)
    builder.add_node("verifier_camera_agent", verifier_camera_agent_node)
    builder.add_node("post_verifier", post_verifier_node)
    builder.add_node("verifier_feedback", verifier_feedback_node)
    builder.add_node("quality_evaluator", quality_evaluator_node)
    builder.add_node("progress_evaluator", progress_evaluator_node)
    builder.add_node("budget_evaluator", budget_evaluator_node)
    builder.add_node("tools", tools_node)
    builder.add_node("todo_commit", todo_commit_node)
    builder.add_node("update_memory", update_memory_node)
    builder.add_node("scene_observe", scene_observe_node)
    builder.add_node("checkpoint_finalize", checkpoint_finalize_node)
    builder.add_node("verify", verify_node)
    builder.add_node("transition_resolver", transition_resolver_node)
    builder.add_node("planner_refresh", planner_refresh_node)
    builder.add_node("finalize", finalize_node)

    builder.add_edge(START, "initialize_request")
    builder.add_conditional_edges("initialize_request", route_after_initialize_request)
    builder.add_conditional_edges("sync_reference_catalog", route_after_sync_reference_catalog)
    builder.add_conditional_edges("prepare_reference_context", route_after_prepare_reference_context)

    builder.add_edge("agent", "turn_dispatch")
    builder.add_conditional_edges("turn_dispatch", route_after_turn_dispatch)
    builder.add_conditional_edges("todo_commit", route_after_todo_commit)

    builder.add_edge("builder_agent", "post_builder")
    builder.add_conditional_edges("post_builder", route_after_post_builder)

    builder.add_edge("verifier_camera_agent", "post_verifier")
    builder.add_conditional_edges("post_verifier", route_after_post_verifier)

    builder.add_edge("verifier_feedback", "quality_evaluator")

    builder.add_edge("tools", "update_memory")
    builder.add_edge("update_memory", "scene_observe")
    builder.add_edge("scene_observe", "verify")
    builder.add_conditional_edges("verify", route_after_verify)

    builder.add_edge("quality_evaluator", "progress_evaluator")
    builder.add_edge("progress_evaluator", "budget_evaluator")
    builder.add_edge("budget_evaluator", "transition_resolver")

    builder.add_conditional_edges("transition_resolver", route_after_transition_resolver)
    builder.add_edge("planner_refresh", "builder_agent")

    builder.add_conditional_edges("checkpoint_finalize", route_after_finalize_checkpoint)
    builder.add_edge("finalize", END)

    return builder
