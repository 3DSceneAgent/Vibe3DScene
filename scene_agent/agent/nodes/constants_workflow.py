"""Workflow constants for routing and role orchestration."""

from __future__ import annotations

from scene_agent.agent.state import TaskMode
from scene_agent.agent.tool_policy import READ_ONLY_TOOLS

# Workflow modes/topology and role labels.
MODE_DIRECT: TaskMode = "direct_mode"
MODE_PLAN: TaskMode = "plan_mode"
TOPOLOGY_SINGLE = "single_agent"
TOPOLOGY_DUAL = "dual_agent"
ROLE_GENERAL = "general"
ROLE_BUILDER = "builder"
ROLE_VERIFIER = "verifier"

# Global defaults for workflow evaluator behavior.
DEFAULT_MAX_PLAN_REPLANS = 2
DEFAULT_SKIP_THRESHOLD = 6
DEFAULT_REPLAN_THRESHOLD = 2

# Deprecated compatibility aliases retained for legacy imports/tests.
MODE_CONVERSATION = "conversation_mode"
MODE_SINGLE_ACTION = "single_action_mode"
CONVERSATION_READ_ONLY_TOOLS: frozenset[str] = READ_ONLY_TOOLS
REQUEST_BUDGET_DEFAULTS: dict[str, dict[str, int]] = {
    MODE_PLAN: {"max_request_agent_turns": -1, "max_request_tool_batches": -1},
    MODE_CONVERSATION: {"max_request_agent_turns": -1, "max_request_tool_batches": -1},
    MODE_SINGLE_ACTION: {"max_request_agent_turns": -1, "max_request_tool_batches": -1},
}
