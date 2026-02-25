# Agentic Workflow / State Refactor 实施记录

更新时间：2026-02-25  
作者：Codex

---

## 1. 目标与范围

本次实现基于文档《agentic-workflow-refactor-proposal-2026-02-25.md》，聚焦两条主线：

1. 工作流重构（workflow）
   - 引入请求级 `route_mode_node`（`conversation_mode / single_action_mode / plan_mode`）。
   - 去除 `agent_decision` 对路由的控制作用，改用客观状态与预算控制。
   - 增加请求级预算（agent turns / tool batches）并在路由中生效。

2. 状态清理（state）
   - 清理冗余和低价值字段。
   - 修正 reducer 语义，减少“状态膨胀 + 语义漂移”。
   - 收敛 finalize 元数据到 `workflow` 字段。

本次未要求保持向后兼容，因此同步更新了单测断言。

---

## 2. 实施过程跟踪（按执行顺序）

### Step A：状态模型清理（`state.py`）

文件：`scene_agent/agent/state.py`

关键变更：

1. 删除未使用/低价值结构：
   - `ReferenceImageInfo`
   - `DiagnosticInfo`
   - `camera_renderings`
   - `reference_images`（state 内）
   - `diagnostics`（state 内）
   - `current_task`
   - `iteration_count`
   - `last_error`
   - `last_scene_observe_round`
   - `agent_decision`（state 字段）

2. 新增/调整关键字段：
   - `task_mode: conversation_mode | single_action_mode | plan_mode`
   - `task_intent`
   - `tool_policy`
   - `request_agent_turns`
   - `request_tool_batches`
   - `max_request_agent_turns`
   - `max_request_tool_batches`
   - `request_stop_reason`
   - `workflow`

3. reducer 修正：
   - `scene_objects` 改为替换语义（`replace_mapping`），避免残留幽灵对象。
   - `persistent_cameras` 改为去重保序（`merge_unique_strings`），避免无限累积。
   - `scene_camera_params` 改为替换语义。

4. `create_todo()` 改造：
   - ID 从 timestamp 改为 `uuid4().hex`，降低碰撞风险。

---

### Step B：节点逻辑重构（`nodes.py`）

文件：`scene_agent/agent/nodes.py`

关键变更：

1. 新增请求路由节点：
   - `route_mode_node()`
   - 通过用户文本和未完成 todo 判定 mode 与 intent。
   - 每次请求初始化预算与计数器。

2. 新增模式/预算策略常量：
   - `MODE_CONVERSATION / MODE_SINGLE_ACTION / MODE_PLAN`
   - `REQUEST_BUDGET_DEFAULTS`
   - `CONVERSATION_READ_ONLY_TOOLS`

3. `agent_node()` 调整：
   - 在原有 allow-list 基础上叠加 mode 策略。
   - `conversation_mode` 下仅允许 read-only 工具。
   - 工具预算耗尽时强制禁用工具调用并注入系统约束提示。

4. `post_agent_node()` 调整：
   - 保留 todo 提取/对齐。
   - 删除 `agent_decision` 提取。
   - 删除 `iteration_count` 逻辑。
   - 新增请求级 turn 计数与预算终止标记。

5. `update_memory_node()` 调整：
   - 新增 `request_tool_batches` 递增。
   - 超预算时设置 `request_stop_reason=tool_batch_budget_exhausted`。

6. `finalize_node()` 调整：
   - 输出从 `agent_decision` 改为 `workflow` 元数据。
   - 新增 `_build_workflow_metadata()`，统一计算 `finish_reason`。
   - `finalize summary` 上下文从 `decision` 改为 `workflow`。

7. 清理 `agent_decision` 相关依赖：
   - 删除 `_extract_agent_decision()`。
   - 删除 `_extract_tagged_json()`。
   - 删除 verification guidance 中对 `next_focus_objects` 的依赖。

---

### Step C：图路由重构（`graph.py`）

文件：`scene_agent/agent/graph.py`

关键变更：

1. 新增图起始节点：
   - `START -> route_mode -> agent`

2. `_route_after_post_agent()` 去耦：
   - 删除 `agent_decision.should_call_tools` 路由分支。
   - 改为依据：
     - 是否有 tool calls
     - 当前 `task_mode`
     - 请求级 turn 预算
     - todo 是否未完成

3. 预算守卫接入：
   - `_route_after_loop_checkpoint()` 支持预算耗尽转 `checkpoint_finalize`。
   - `_route_after_todo_check()` 增加预算耗尽直接 `finalize`。

4. 新增辅助判断函数：
   - `_task_mode()`
   - `_has_unfinished_todos()`
   - `_agent_turn_budget_exhausted()`

---

### Step D：Prompt 与测试同步

文件：`scene_agent/agent/prompts.py`

关键变更：

1. 移除 `<agent_decision>` 强制输出要求和示例。
2. 增加执行行为约束：不输出 XML/JSON 控制包装。

文件：`tests/unit/test_agent_workflow_control.py`

关键变更：

1. 删除对 `agent_decision/iteration_count` 的旧断言与输入依赖。
2. `finalize_node` 断言改为 `workflow` 输出。
3. 新增 `route_mode_node` 的分类测试（conversation/single_action/plan）。

---

## 3. 行为变化摘要

## 3.1 路由层

旧行为：
- `post_agent` 通过 `agent_decision` 影响是否重试 `agent`。

新行为：
- `route_mode_node` 先做 mode 路由。
- `post_agent` 不再提供控制信号。
- 路由由客观状态 + 预算决定。

## 3.2 状态层

旧行为：
- 存在多处冗余字段和 reducer 累积风险（如 camera list、scene objects merge）。

新行为：
- 状态字段收敛，面向“请求预算 + 任务模式 + workflow 元数据”。
- `scene_objects` 替换式，`persistent_cameras` 去重式。

## 3.3 最终输出层

旧行为：
- finalize 回写 `agent_decision.workflow_status/finish_reason`。

新行为：
- finalize 回写 `workflow`，并保持用户可读总结文本输出。

---

## 4. 测试与验证

执行命令与结果：

1. 语法编译检查：
```bash
python -m py_compile scene_agent/agent/state.py scene_agent/agent/nodes.py scene_agent/agent/graph.py scene_agent/agent/prompts.py tests/unit/test_agent_workflow_control.py
```
结果：通过。

2. 核心回归子集：
```bash
pytest -q tests/unit/test_agent_workflow_control.py tests/unit/test_verify_node.py tests/unit/test_scene_observe_camera_context.py tests/unit/test_render_image_pipeline.py
```
结果：通过（74 passed）。

3. 全量 unit：
```bash
pytest -q tests/unit
```
结果：通过（194 passed, 4 skipped）。

4. 额外集成抽样：
```bash
pytest -q tests/integration/test_chat_stream.py
```
结果：通过（5 passed, 1 skipped）。

5. 合同测试说明：
```bash
pytest -q tests/contract
```
结果：1 失败（`test_reference_images_contract`）。  
失败原因为 owner-proxy + multipart 请求体重复消费（`RuntimeError: Stream consumed`），属于线程 owner 转发路径问题，不是本次 workflow/state 改造直接引入；需在 owner proxy 上传路径单独修复。

---

## 5. 变更文件清单

1. `scene_agent/agent/state.py`
2. `scene_agent/agent/nodes.py`
3. `scene_agent/agent/graph.py`
4. `scene_agent/agent/prompts.py`
5. `tests/unit/test_agent_workflow_control.py`
6. `docs/codex_created/agentic-workflow-refactor-implementation-2026-02-25.md`（本文件）

---

## 6. 后续建议（非阻塞）

1. 将 `route_mode_node` 的规则分类升级为结构化 LLM 分类器（并保留规则 fallback）。
2. 将 evaluator 进一步拆分为 `quality/progress/budget/transition_resolver` 四个独立节点。
3. 修复 owner proxy 对 multipart 请求的转发与回退复读能力，恢复 `tests/contract` 稳定通过。

