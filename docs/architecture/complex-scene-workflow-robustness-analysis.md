# 复杂 3D 场景构建下的 Workflow 鲁棒性分析

更新时间：2026-02-28

## 1. 背景与问题定义

本文从算法与 Agent 系统设计角度，分析当前后端 workflow 在"用户请求构建复杂 3D 场景（多物体、多轮迭代、长时运行）"下的能力边界与潜在风险，重点覆盖：

- `single_agent` 拓扑的稳定性与收敛性
- `dual_agent` 拓扑在 plan_mode 下的收益与额外复杂度
- 两类拓扑共享的系统性瓶颈
- 长时 HTTP 连接优化方案
- 面向工程落地的改进路线

---

## 2. 结论先行（Executive Summary）

当前实现对中等复杂任务可用，但对"高复杂、长时、多轮修复"的场景仍存在明显鲁棒性缺口。核心问题并不只是拓扑选择（single vs dual），而是三项基础能力尚未完备：

1. **长时上下文管理（Context 管控）**
2. **硬预算与停机保护（Budget Guardrails）**
3. **收敛/振荡检测（Convergence Detection）**

在这三项未补齐前，single-agent 与 dual-agent 都可能出现长循环、质量退化、成本失控；dual-agent 还会引入额外调用开销与协同复杂度。

---

## 3. Single-Agent 拓扑

### 3.1 能力与优势

- **执行链路简洁**：单角色负责计划+执行，状态流转清晰，调试成本低。
- **上下文一致性较好**：不存在多角色切换造成的语义断裂。
- **Render-and-Verify 闭环已具备**：变更后自动观察与验证，可形成迭代修复循环。
- **部分消息去累积**：视觉消息使用固定 ID 替换（`RENDER_VISION_MESSAGE_ID` / `SCENE_OBSERVE_MESSAGE_ID`），避免某些多模态消息无限追加。

### 3.2 风险列表（按严重程度排序）

#### P0 — 致命 / 长任务必触发

| # | 风险 | 现象 | 根因 |
|---|------|------|------|
| S1 | **Context Window 线性膨胀** | 30-40 轮后推理质量退化、远端目标遗忘、甚至超限截断 | `AIMessage`/`ToolMessage`/`verification` 全量累积，无压缩/摘要/滑窗机制 |
| S2 | **plan_mode 无硬预算** | Agent 可在 verify-fix 循环中无限运行，成本失控 | `max_request_agent_turns = -1`，`max_request_tool_batches = -1`，`max_plan_replans = -1` |
| S3 | **验证振荡无收敛检测** | "scale too large → fix → scale too small → fix → ..." 死循环 | 无连续相似 mismatch 识别；无策略升级/熔断路径 |

#### P1 — 严重 / 复杂任务高概率触发

| # | 风险 | 现象 | 根因 |
|---|------|------|------|
| S4 | **VLM 验证假阳/假阴** | 空间关系误判、遮挡导致误 pass/误 fail | VLM 空间推理弱；catastrophic 检测依赖硬编码阈值（`stddev ≤ 2.0` 等） |
| S5 | **停滞检测延迟** | 浪费 6+ 轮才识别到 blocked | `TODO_CHECK_INTERVAL_ROUNDS = 3` × `TODO_STAGNATION_LIMIT = 2`；无法识别"抖动式伪进展" |
| S6 | **单次 HTTP 请求执行** | 长任务 HTTP 超时/断连 → 全部丢失 | `ainvoke`/`astream` 全量在一次 request 中完成，无 pause/resume |

#### P2 — 中等 / 大规模场景中显现

| # | 风险 | 现象 | 根因 |
|---|------|------|------|
| S7 | **Todo 结构扁平** | 执行顺序错位、返工增多 | 缺少依赖关系、阶段里程碑、优先级机制 |
| S8 | **工具故障级联** | 单工具失败后 state 不一致，后续操作基于错误场景 | 重试仅 2 次；无事务回滚；错误 ToolMessage 继续进入 agent context |

---

## 4. Dual-Agent 拓扑

> 当前仅在 `plan_mode` 生效；非 plan_mode 强制降级 single_agent。

### 4.1 能力与潜在收益

- **角色分工清晰**：Builder 负责构建，Verifier 独立审校。
- **工具域隔离**：Builder 排除相机工具，Verifier 仅允许相机/渲染工具，降低误改概率。
- **引入 replan 通道**：可依据验证反馈触发轻量重规划（`planner_refresh`）。
- **Builder 停滞检测**：`builder_stall_count` 追踪无 tool call 的空转，触发 replan 或降级。

### 4.2 风险列表（按严重程度排序）

#### P0 — 致命 / 继承且放大 single-agent 问题

| # | 风险 | 现象 | 根因 |
|---|------|------|------|
| D1 | **共享 Context 膨胀速率翻倍** | 双角色输出叠加于同一 messages 序列，context 增速 ≈ 1.5-2× | Builder + Verifier 的 AIMessage 全部追加；role private memory 仅保留 1200 字符摘要 |
| D2 | **同模型同偏置** | Verifier "审核通过"的结果不可靠 | Builder/Verifier 共用同一 VLM 实例；系统性偏差被自洽放大而非纠正 |
| D3 | **无硬预算**（继承 S2） | 双角色循环 + replan 共同放大无限循环概率 | plan_mode 三维预算均为 -1 |

#### P1 — 严重 / 双角色交互特有问题

| # | 风险 | 现象 | 根因 |
|---|------|------|------|
| D4 | **Verifier 工具调用引入额外闭环** | Verifier 相机操作不当 → 产生误导性渲染 → 错误 feedback 传给 Builder | Verifier 是工具调用 agent 而非纯文本评审器；其操作也触发完整 verify 链 |
| D5 | **planner_refresh 粒度偏轻** | Replan 只追加 1-2 个 fix todo，不重审全局策略 | 不删除/重排现有 todo；不评估策略是否根本不可行；不考虑换工具/换方法 |
| D6 | **VLM 调用效率低** | 一个 import+adjust 操作至少 3-4 次 VLM call（vs single-agent 的 1-2 次） | Builder turn + Verifier turn + verify call；20+ 物体场景累积开销 3-4× |

#### P2 — 中等 / 可控但需关注

| # | 风险 | 现象 | 根因 |
|---|------|------|------|
| D7 | **Role Private Memory 极其有限** | 角色间只能通过 messages 隐式通信 | 每个 role 只保留 `last_action_summary[:1200]`，无累积知识库 |
| D8 | **Verifier Feedback 噪声** | 错误的 feedback 导致 builder 做无效/有害的修复 | Feedback 来自同模型验证；如果 verify 本身误判，feedback 全链路放大 |

---

## 5. 两类拓扑共享的系统性瓶颈

按影响面从大到小排列：

| 优先级 | 瓶颈 | 影响 |
|--------|------|------|
| **P0** | 无 Context 压缩/管理 | 长任务必崩，质量递减 |
| **P0** | plan_mode 无硬预算 | 无限循环、成本失控 |
| **P0** | 无收敛/振荡检测 | verify-fix 死循环无法自动终止 |
| **P1** | 长任务单请求执行模型 | HTTP 超时、断连丢失进度 |
| **P1** | 进度度量偏弱 | 难区分"慢进展"与"真停滞" |
| **P1** | 扁平 Todo 无依赖 | 执行顺序混乱，返工增多 |
| **P2** | 缺少运行时 observability | 无 token/cost/turns 面板 |
| **P2** | 无失败案例归档与回归 | 同类问题反复出现 |

---

## 6. 长时 HTTP 连接优化方案

当前实现为 SSE 流式 + keepalive 心跳模型（`/chat/stream`），一次 `astream` 在一个 HTTP 请求内完成。对于复杂场景（可能运行 10-30 分钟），面临连接中断、代理超时、客户端异常等风险。以下按推荐程度排列可选方案。

### 6.1 方案 A：异步任务 + 轮询/订阅（推荐首选）

**核心思路**：将 plan_mode 长任务与 HTTP 请求生命周期解耦。

```text
POST /chat          →  返回 { job_id, status: "running" }（立即响应）
GET  /chat/status   →  返回 { status, progress, latest_message, ... }
GET  /chat/events   →  SSE 流，推送增量事件（可选）
POST /chat/cancel   →  取消运行中的任务
POST /chat/resume   →  从 checkpoint 恢复执行
```

**优势**：
- HTTP 请求秒级返回，无超时风险
- 客户端断连不影响后端执行
- 天然支持 pause/resume/cancel
- 可在移动端、弱网环境稳定工作
- 与现有 LangGraph checkpointer（Redis）天然契合

**实现路径**：
- 后端：`astream` 运行在独立 `asyncio.Task` 中，绑定 `job_id`
- 进度：每个 evaluator 节点写入 Redis（或内存）进度快照
- 前端：轮询 `/chat/status` 或监听 `/chat/events` SSE
- 断线恢复：客户端重新连接 `/chat/events?from_seq=N`，补发遗漏事件

**兼容性**：
- 简单请求（conversation_mode / single_action_mode）仍可走同步 `/chat` 或短 SSE
- 只有 plan_mode 长任务才切到 async job 模式
- 可通过 router 的 `task_mode` 判断自动路由

### 6.2 方案 B：分阶段执行（Phase-Based Execution）

**核心思路**：将一个大 plan 拆分为多个 phase，每个 phase 是一个独立 HTTP 请求。

```text
请求 1: "Create living room scene"
  → Router: plan_mode, 生成 todo 列表
  → 执行 Phase 1 (layout + major objects)
  → 返回 { phase: 1, status: "phase_complete", next_phase: 2 }

请求 2: (自动或手动触发)
  → 继续 Phase 2 (details + materials)
  → 返回 { phase: 2, status: "phase_complete", next_phase: 3 }

请求 3:
  → Phase 3 (refinement + final verify)
  → 返回 { status: "completed" }
```

**优势**：
- 每个 phase 是短时请求（2-5 分钟），HTTP 安全范围内
- 用户可在 phase 间审查/调整方向
- 自然的进度里程碑

**劣势**：
- 需要 phase 边界定义策略（按物体类别/按空间区域/按 todo 数量切分）
- 跨 phase 状态一致性依赖 checkpointer 可靠性

### 6.3 方案 C：WebSocket 持久连接

**核心思路**：建立 WebSocket 双向通道，全生命周期在同一连接中完成。

```text
ws://host/chat/ws?thread_id=xxx
  → 客户端发送 { message, ... }
  → 服务端推送增量事件
  → 支持客户端中途发送 cancel/pause 指令
```

**优势**：
- 双向通信，客户端可随时干预（暂停/取消/追加指令）
- 无 HTTP 超时约束
- 实时性最优

**劣势**：
- 实现与运维复杂度高（WebSocket 需独立负载均衡策略）
- 客户端断线 → 需要完整重连+状态同步机制
- 与现有 NGINX + owner-proxy 架构的兼容需额外工作
- 移动端 WebSocket 稳定性差

### 6.4 方案 D：增强现有 SSE（最小改动）

**核心思路**：保留当前 SSE 架构，增加断线恢复与进度快照。

改进点：
- **事件序号化**：每个 SSE event 带递增 `seq`，客户端重连时带 `Last-Event-ID` 补发
- **心跳增强**：心跳包中携带进度摘要（`{ turns, tool_batches, completed_todos, ... }`）
- **超时动态延长**：plan_mode 下将 `api_stream_timeout_seconds` 设为更大值或无限
- **Client Abort 保护**：检测到客户端断连后继续在后台执行至 checkpoint，而非直接终止

**优势**：改动量最小，前端变化少
**劣势**：本质上仍是长连接模型，无法根治代理层超时和弱网问题

### 6.5 方案对比

| 维度 | A: 异步任务 | B: 分阶段 | C: WebSocket | D: 增强 SSE |
|------|:-----------:|:---------:|:------------:|:-----------:|
| **HTTP 超时风险** | 根治 | 根治 | 根治 | 缓解 |
| **断连恢复** | 天然支持 | 天然支持 | 需额外实现 | 部分支持 |
| **实现复杂度** | 中等 | 中等 | 高 | 低 |
| **前端改动量** | 中等 | 小 | 大 | 最小 |
| **NGINX 兼容性** | 好 | 好 | 需配置 | 好 |
| **用户交互体验** | 好（实时进度） | 好（阶段审查） | 最优 | 一般 |
| **与 checkpointer 契合度** | 高 | 高 | 中等 | 低 |

### 6.6 推荐路线

1. **短期（1-2 周）**：采用方案 D，增加事件序号 + 心跳进度摘要 + plan_mode 超时延长，快速改善体验。
2. **中期（3-4 周）**：实现方案 A，plan_mode 走异步任务模式，短模式保持同步 SSE。
3. **长期（可选）**：在方案 A 基础上叠加方案 B 的 phase 拆分，实现"阶段提交 + 用户审查"的交互模式。

---

## 7. 风险改进路线（按优先级）

### P0 — 必须尽快完成

| # | 改进项 | 目标 | 涉及模块 |
|---|--------|------|----------|
| 1 | **Context 管理** | 引入"近期原文 + 历史摘要"双层记忆；限制注入 messages 条数 | `nodes/shared.py`, `state.py`, 新增 `context_manager.py` |
| 2 | **预算护栏** | plan_mode 设置 agent/tool/replan 合理上限（如 50/40/3），用户可配置 | `nodes/shared.py`, `nodes/router.py`, `workflow_profiles.py` |
| 3 | **收敛检测** | 连续 N 轮相似 mismatch → 策略切换/跳过/熔断 | `nodes/evaluators.py`, 新增 `convergence.py` |

### P1 — 显著提升鲁棒性

| # | 改进项 | 目标 | 涉及模块 |
|---|--------|------|----------|
| 4 | **长任务异步执行** | plan_mode 走 async job 模式（方案 A） | `routes_chat.py`, 新增 `job_manager.py` |
| 5 | **层级化 Todo** | 支持 phase/依赖/优先级，减少返工 | `state.py`, `nodes/execution.py` |
| 6 | **Dual-Agent 去同质化** | Verifier 使用差异化模型或差异化 prompt/temperature | `graph.py`, `nodes/agents.py` |
| 7 | **停滞检测增强** | 引入语义级进度追踪（不仅 snapshot 比对） | `nodes/execution.py` |

### P2 — 体验与可观测性

| # | 改进项 | 目标 | 涉及模块 |
|---|--------|------|----------|
| 8 | **运行时观测面板** | token/cost/turns/replan/stagnation 指标与告警 | 新增 `observability.py` |
| 9 | **SSE 断线恢复** | 事件序号 + Last-Event-ID 补发 | `routes_chat.py` |
| 10 | **失败案例回归** | 沉淀振荡/超限案例为自动化测试 | `tests/` |

---

## 8. 建议的架构选择（当前阶段）

- **默认策略**：在 P0 未完成前，建议默认 `single_agent` 作为主执行路径（更易控、更易调试）。
- **dual-agent 适用场景**：用于高价值、需要强审校的 plan_mode 任务，但需配合预算与收敛护栏。
- **落地顺序**：先补齐共性基础设施（P0），再扩大 dual-agent 覆盖范围。

---

## 9. 最终判断

对于"很复杂的 3D 场景构建"与"长时间稳定运行"目标，当前 workflow 具备雏形能力，但**尚未达到强鲁棒工程态**。短板主要在长时控制能力，而非单点算法技巧。优先完成 Context/Budget/Convergence 三大基础能力升级 + 长任务异步化后，single 与 dual 两种拓扑都能显著提升稳定性与可控成本。
