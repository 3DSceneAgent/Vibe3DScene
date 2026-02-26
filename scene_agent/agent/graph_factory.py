"""
Graph assembly helpers for workflow topology composition.
"""
from __future__ import annotations

from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from scene_agent.agent.state import AgentState


def build_agent_state_graph(
    *,
    route_mode_node: Callable[..., Any],
    agent_node: Callable[..., Any],
    post_agent_node: Callable[..., Any],
    builder_agent_node: Callable[..., Any],
    post_builder_node: Callable[..., Any],
    verifier_camera_agent_node: Callable[..., Any],
    post_verifier_node: Callable[..., Any],
    verifier_feedback_node: Callable[..., Any],
    tools_node: Any,
    update_memory_node: Callable[..., Any],
    scene_observe_node: Callable[..., Any],
    checkpoint_loop_node: Callable[..., Any],
    checkpoint_finalize_node: Callable[..., Any],
    todo_check_node: Callable[..., Any],
    verify_node: Callable[..., Any],
    transition_resolver_node: Callable[..., Any],
    planner_refresh_node: Callable[..., Any],
    finalize_node: Callable[..., Any],
    route_after_mode: Callable[..., Any],
    route_after_post_agent: Callable[..., Any],
    route_after_post_builder: Callable[..., Any],
    route_after_post_verifier: Callable[..., Any],
    route_after_verify: Callable[..., Any],
    route_after_transition_resolver: Callable[..., Any],
    route_after_loop_checkpoint: Callable[..., Any],
    route_after_finalize_checkpoint: Callable[..., Any],
    route_after_todo_check: Callable[..., Any],
) -> StateGraph:
    builder = StateGraph(AgentState)

    builder.add_node("route_mode", route_mode_node)
    builder.add_node("agent", agent_node)
    builder.add_node("post_agent", post_agent_node)
    builder.add_node("builder_agent", builder_agent_node)
    builder.add_node("post_builder", post_builder_node)
    builder.add_node("verifier_camera_agent", verifier_camera_agent_node)
    builder.add_node("post_verifier", post_verifier_node)
    builder.add_node("verifier_feedback", verifier_feedback_node)
    builder.add_node("tools", tools_node)
    builder.add_node("update_memory", update_memory_node)
    builder.add_node("scene_observe", scene_observe_node)
    builder.add_node("checkpoint_loop", checkpoint_loop_node)
    builder.add_node("checkpoint_finalize", checkpoint_finalize_node)
    builder.add_node("todo_check", todo_check_node)
    builder.add_node("verify", verify_node)
    builder.add_node("transition_resolver", transition_resolver_node)
    builder.add_node("planner_refresh", planner_refresh_node)
    builder.add_node("finalize", finalize_node)

    builder.add_edge(START, "route_mode")
    builder.add_conditional_edges("route_mode", route_after_mode)

    builder.add_edge("agent", "post_agent")
    builder.add_conditional_edges("post_agent", route_after_post_agent)

    builder.add_edge("builder_agent", "post_builder")
    builder.add_conditional_edges("post_builder", route_after_post_builder)

    builder.add_edge("verifier_camera_agent", "post_verifier")
    builder.add_conditional_edges("post_verifier", route_after_post_verifier)

    builder.add_edge("tools", "update_memory")
    builder.add_edge("update_memory", "scene_observe")
    builder.add_edge("scene_observe", "verify")
    builder.add_conditional_edges("verify", route_after_verify)

    builder.add_edge("verifier_feedback", "transition_resolver")
    builder.add_conditional_edges("transition_resolver", route_after_transition_resolver)
    builder.add_edge("planner_refresh", "builder_agent")

    builder.add_conditional_edges("checkpoint_loop", route_after_loop_checkpoint)
    builder.add_conditional_edges("checkpoint_finalize", route_after_finalize_checkpoint)
    builder.add_conditional_edges("todo_check", route_after_todo_check)
    builder.add_edge("finalize", END)

    return builder
