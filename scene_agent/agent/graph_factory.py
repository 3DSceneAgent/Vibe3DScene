"""Graph assembly helpers for workflow topology composition."""

from __future__ import annotations

from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from scene_agent.agent.state import AgentState


def build_agent_state_graph(
    *,
    initialize_request_node: Callable[..., Any],
    sync_reference_catalog_node: Callable[..., Any],
    prepare_reference_context_node: Callable[..., Any],
    router_node: Callable[..., Any],
    plan_node: Callable[..., Any],
    agent_node: Callable[..., Any],
    turn_dispatch_node: Callable[..., Any],
    builder_agent_node: Callable[..., Any],
    post_builder_node: Callable[..., Any],
    verifier_agent_node: Callable[..., Any],
    verifier_feedback_node: Callable[..., Any],
    evaluator_node: Callable[..., Any],
    tools_node: Any,
    update_memory_node: Callable[..., Any],
    scene_observe_node: Callable[..., Any],
    verify_node: Callable[..., Any],
    planner_refresh_node: Callable[..., Any],
    finalize_node: Callable[..., Any],
    route_after_initialize_request: Callable[..., Any],
    route_after_sync_reference_catalog: Callable[..., Any],
    route_after_prepare_reference_context: Callable[..., Any],
    route_after_router: Callable[..., Any],
    route_after_plan_node: Callable[..., Any],
    route_after_turn_dispatch: Callable[..., Any],
    route_after_post_builder: Callable[..., Any],
    route_after_verifier_feedback: Callable[..., Any],
    route_after_update_memory: Callable[..., Any],
    route_after_scene_observe: Callable[..., Any],
    route_after_evaluator: Callable[..., Any],
) -> StateGraph:
    builder = StateGraph(AgentState)

    builder.add_node("initialize_request", initialize_request_node)
    builder.add_node("sync_reference_catalog", sync_reference_catalog_node)
    builder.add_node("prepare_reference_context", prepare_reference_context_node)
    builder.add_node("router", router_node)
    builder.add_node("plan_node", plan_node)

    builder.add_node("agent", agent_node)
    builder.add_node("turn_dispatch", turn_dispatch_node)
    builder.add_node("builder_agent", builder_agent_node)
    builder.add_node("post_builder", post_builder_node)
    builder.add_node("verifier_agent", verifier_agent_node)
    builder.add_node("verifier_feedback", verifier_feedback_node)

    builder.add_node("tools", tools_node)
    builder.add_node("update_memory", update_memory_node)
    builder.add_node("scene_observe", scene_observe_node)
    builder.add_node("verify", verify_node)
    builder.add_node("evaluator", evaluator_node)
    builder.add_node("planner_refresh", planner_refresh_node)
    builder.add_node("finalize", finalize_node)

    builder.add_edge(START, "initialize_request")
    builder.add_conditional_edges("initialize_request", route_after_initialize_request)
    builder.add_conditional_edges("sync_reference_catalog", route_after_sync_reference_catalog)
    builder.add_conditional_edges("prepare_reference_context", route_after_prepare_reference_context)
    builder.add_conditional_edges("router", route_after_router)
    builder.add_conditional_edges("plan_node", route_after_plan_node)

    builder.add_edge("agent", "turn_dispatch")
    builder.add_conditional_edges("turn_dispatch", route_after_turn_dispatch)

    builder.add_edge("builder_agent", "post_builder")
    builder.add_conditional_edges("post_builder", route_after_post_builder)

    builder.add_edge("verifier_agent", "verifier_feedback")
    builder.add_conditional_edges("verifier_feedback", route_after_verifier_feedback)

    builder.add_edge("tools", "update_memory")
    builder.add_conditional_edges("update_memory", route_after_update_memory)

    builder.add_conditional_edges("scene_observe", route_after_scene_observe)
    builder.add_edge("verify", "evaluator")

    builder.add_conditional_edges("evaluator", route_after_evaluator)
    builder.add_edge("planner_refresh", "builder_agent")
    builder.add_edge("finalize", END)

    return builder
