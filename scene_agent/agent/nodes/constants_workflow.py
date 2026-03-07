"""Workflow constants for routing and role orchestration."""

from __future__ import annotations

from scene_agent.agent.state import TaskMode
from scene_agent.agent.tool_policy import READ_ONLY_TOOLS

# Workflow modes/topology and role labels.
MODE_CONVERSATION: TaskMode = "conversation_mode"
MODE_SINGLE_ACTION: TaskMode = "single_action_mode"
MODE_PLAN: TaskMode = "plan_mode"
TOPOLOGY_SINGLE = "single_agent"
TOPOLOGY_DUAL = "dual_agent"
ROLE_GENERAL = "general"
ROLE_BUILDER = "builder"
ROLE_VERIFIER = "verifier"

# Global defaults for plan-mode control budgets.
DEFAULT_MAX_PLAN_REPLANS = 3
REQUEST_BUDGET_DEFAULTS: dict[TaskMode, dict[str, int]] = {
    MODE_CONVERSATION: {"max_request_agent_turns": 2, "max_request_tool_batches": 0},
    MODE_SINGLE_ACTION: {"max_request_agent_turns": 3, "max_request_tool_batches": 1},
    MODE_PLAN: {"max_request_agent_turns": 50, "max_request_tool_batches": 40},
}
CONVERSATION_READ_ONLY_TOOLS: frozenset[str] = READ_ONLY_TOOLS
