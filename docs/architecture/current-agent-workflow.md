# 当前后端 Agent Workflow 架构（Router / Single / Dual）

更新时间：2026-02-27

本文基于当前后端实现代码核对，重点覆盖：
- API Router 到 Graph 执行入口
- `route_mode` 路由器（含 topology 选择）
- `single_agent` 拓扑
- `dual_agent` 拓扑（仅 `plan_mode`）
- 与近期三份方案/实现文档对应的落地状态

核对的核心代码：
- `scene_agent/interfaces/api/routes_chat.py`
- `scene_agent/interfaces/api/shared.py`
- `scene_agent/agent/graph.py`
- `scene_agent/agent/graph_factory.py`
- `scene_agent/agent/nodes/router.py`
- `scene_agent/agent/nodes/agents.py`
- `scene_agent/agent/nodes/evaluators.py`
- `scene_agent/agent/nodes/execution.py`
- `scene_agent/agent/nodes/verification.py`
- `scene_agent/agent/tool_policy.py`
- `scene_agent/agent/workflow_profiles.py`
- `scene_agent/memory/reference_image_memory.py`

---

## 1. API Router 与 Graph 入口

```mermaid
flowchart LR
    A["Client / Web / CLI"] --> B["/chat or /chat/stream"]
    B --> C["claim_or_proxy_request"]
    C --> D["resolve_thread_vlm_for_chat"]
    D --> E["get_agent(thread_id)"]

    E --> F{"graph exists + VLM match?"}
    F -->|no| G["create_agent_graph"]
    G --> H["get_blender_tools + bind_tools"]
    H --> I["build_agent_state_graph + compile(checkpointer)"]
    F -->|yes| J["reuse graph"]
    I --> J

    B --> K["resolve_enabled_tool_names"]
    K --> L["initial state: messages, enabled_tool_names, task_id, workflow_topology_request, memory_profile_request"]
    J --> M["agent.ainvoke / agent.astream"]
    L --> M
```

关键点：
- 请求级可传 `workflow_topology`、`memory_profile`，在 `routes_chat.py` 透传为 `workflow_topology_request`、`memory_profile_request`。
- `get_agent(thread_id)` 按线程缓存 graph，并在 provider/model 变化时重建并迁移状态。
- checkpointer 优先 Redis，不可用时回退 `InMemorySaver`（`redis_checkpointer.py`）。

---

## 2. Router 架构（`route_mode` + topology 解析）

```mermaid
flowchart TD
    A["START"] --> B["route_mode_llm_node"]
    B --> C{"unfinished todos > 0?"}
    C -->|yes| D["force: plan_mode + continue_existing_plan"]
    C -->|no| E["LLM RouterDecision(intent/mode/confidence/clarification)"]

    D --> F["resolve_workflow_topology + resolve_memory_profile"]
    E --> F

    F --> G{"need_clarification OR confidence < 0.65"}
    G -->|yes| H["clarification_node"]
    H --> I["END (clarification_required)"]

    G -->|no| J{"plan_mode + dual_agent?"}
    J -->|yes| K["builder_agent"]
    J -->|no| L["agent"]
```

关键点：
- `dual_agent` 只在 `plan_mode` 生效，非 `plan_mode` 强制降级 `single_agent`。
- `auto` topology 由 `ENABLE_PLANMODE_DUAL_AGENT` 控制。
- Router 同时初始化预算与角色状态：`max_request_agent_turns`、`max_request_tool_batches`、`active_role` 等。

---

## 3. Single-Agent 拓扑（当前主干）

```mermaid
flowchart TD
    A["agent"] --> B["post_agent"]
    B --> C{"AIMessage has tool_calls?"}

    C -->|yes| D["tools"]
    D --> E["update_memory"]
    E --> F["scene_observe"]
    F --> G["verify"]
    G --> H["quality_evaluator"]

    C -->|no| H

    H --> I["progress_evaluator"]
    I --> J["budget_evaluator"]
    J --> K["transition_resolver"]

    K -->|agent| A
    K -->|checkpoint_finalize| L["checkpoint_finalize"]

    L --> M{"task_mode == plan_mode?"}
    M -->|no| Q["END"]
    M -->|yes| N{"todo_check_gate.should_run?"}
    N -->|yes| O["todo_check"]
    N -->|no| P["finalize"]

    O --> R{"status"}
    R -->|completed/not_applicable| P
    R -->|continue/blocked| A
    P --> Q
```

关键点：
- `verify` 后固定进入 evaluator 链（`quality -> progress -> budget -> transition_resolver`）。
- `verify` 的 catastrophic 只产出信号，不再自动注入恢复工具调用。
- `scene_observe` 仅在最近工具批次包含 scene mutation 时生效。
- 仅 `plan_mode` 会进入 `finalize` 节点；`conversation_mode` / `single_action_mode` 在 `checkpoint_finalize` 后直接 `END`。

---

## 4. Dual-Agent 拓扑（仅 `plan_mode`）

```mermaid
flowchart TD
    A["builder_agent"] --> B["post_builder"]
    B --> C{"builder has tool_calls?"}

    C -->|yes| D["tools -> update_memory -> scene_observe -> verify"]
    D --> E["quality_evaluator"]

    C -->|no| F["verifier_camera_agent"]
    F --> G["post_verifier"]
    G --> H{"verifier has tool_calls?"}

    H -->|yes| D
    H -->|no| I["verifier_feedback"]
    I --> E

    E --> J["progress_evaluator"]
    J --> K["budget_evaluator"]
    K --> L["transition_resolver"]

    L -->|builder_agent| A
    L -->|planner_refresh| M["planner_refresh"]
    M --> A
    L -->|checkpoint_finalize| N["checkpoint_finalize"]

    N --> O{"todo_check_gate.should_run?"}
    O -->|yes| P["todo_check"]
    O -->|no| Q["finalize"]
    P --> R{"status"}
    R -->|completed/not_applicable| Q
    R -->|continue/blocked| A
    Q --> S["END"]
```

关键点：
- `verifier_camera_agent` 是可调用相机/渲染工具的 agent，不是纯文本评审器。
- `builder` 与 `verifier` 工具域由 `tool_policy.py` 控制：
  - `builder_default` 默认排除相机/渲染工具域。
  - `verifier_default` 仅允许相机/渲染观察工具域。
- `transition_resolver` 是确定性规则节点，优先级：
  1. budget_exhausted
  2. done
  3. catastrophic
  4. replan
  5. continue

---

## 5. 图片资产与 Verify 路径（与 workflow 绑定）

```mermaid
flowchart LR
    A["POST /threads/{thread_id}/images"] --> B["ImageAssetMemory.add_assets"]
    C["GET /threads/{thread_id}/images"] --> D["ImageAssetMemory.list_assets"]

    E["verify_node"] --> F["resolve_verification_assets"]
    F --> G["ensure_auto_bindings(task_id, preferred_role_by_mode)"]
    G --> H["resolve_assets(task_id + global, role_filter)"]
    H --> I["verify_render_with_references"]
    I --> J["ToolMessage(name=verification)"]
```

关键点：
- 对外公开接口只有 `/images`（`reference-images`、`image-bindings` 已从 API 层移除）。
- 内部仍保留 role 绑定模型：`question_image/object_reference/scene_reference/style_reference/verification_reference`。
- `verify_node` 会按 `task_mode` 自动选择/补齐绑定角色，再做验证。

---

## 6. 与三份文档的对应关系（落地到代码）

### 6.1 `agentic-workflow-refactor-proposal-2026-02-25.md`
已落地：
- LLM 结构化 Router + clarification 分支。
- single/dual 共用 evaluator 主链。
- `transition_resolver` 确定性路由。

代码位置：
- `scene_agent/agent/nodes/router.py`
- `scene_agent/agent/nodes/evaluators.py`
- `scene_agent/agent/graph_factory.py`

### 6.2 `agentic-workflow-abstraction-and-planmode-dual-agent-plan-2026-02-26.md`
已落地：
- 抽象层：`workflow_profiles.py`、`tool_policy.py`、`memory_scope.py`、`graph_factory.py`。
- `plan_mode` 下可选 dual-agent，且只在 `plan_mode` 生效。
- `planner_refresh` 最小重规划节点已接入主图。

代码位置：
- `scene_agent/agent/workflow_profiles.py`
- `scene_agent/agent/tool_policy.py`
- `scene_agent/agent/memory_scope.py`
- `scene_agent/agent/nodes/evaluators.py`

### 6.3 `image-asset-workflow-refactor-implementation-2026-02-26.md`
已落地：
- 图片资产 API 统一为 `/threads/{thread_id}/images`。
- `verify` 走 task + role 语义解析，并支持自动绑定。
- legacy `reference-images` 仅在 memory/store 层保留兼容包装，不再公开 API。

代码位置：
- `scene_agent/interfaces/api/routes_assets.py`
- `scene_agent/agent/nodes/verification.py`
- `scene_agent/memory/reference_image_memory.py`
- `scene_agent/memory/reference_image_store.py`

---

## 7. 当前实现的关键差异说明（相对早期版本）

- 主图不再接 `blocked_recovery -> blocked_recovery_action`，恢复策略改为由 agent 根据 catastrophic 信号自主修复。
- Router / Evaluator / Topology 已模块化拆分，不再集中在单一 `nodes.py` 文件。
- API 层已拆为多 router 模块（`routes_chat/routes_assets/routes_scene/routes_runtime/routes_system`），不再是单一 `api.py`。
- SSE 用户消息流对内部节点做了可见性约束：`route_mode`（以及 `verify` 的非 tool token）不会直接显示到聊天消息中。
