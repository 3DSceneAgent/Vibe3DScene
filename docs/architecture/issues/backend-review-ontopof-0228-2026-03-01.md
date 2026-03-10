# 后端代码审查报告 — 2026-03-01

审查范围：`context_manager`、`convergence` 收敛检测、SSE 长连接稳定性（含前端 resume 逻辑）。

代码核对路径：
- `scene_agent/agent/context_manager.py`
- `scene_agent/agent/convergence.py`
- `scene_agent/agent/nodes/evaluators.py`
- `scene_agent/agent/nodes/shared.py`
- `scene_agent/interfaces/api/routes_chat.py`
- `scene_agent/interfaces/api/shared.py`
- `scene_agent/blender/session_manager.py`
- `scene_agent/session/session_coordinator.py`
- `web/src/api/client.ts`

---

## 优先级 / 风险汇总

| # | 等级 | 模块 | 问题简述 |
|---|------|------|---------|
| 1 | **P1 — 功能失效** | Frontend SSE | `reader.read()` 抛出网络错误时 resume 逻辑永远不触发 |
| 2 | **P1 — 过早熔断** | Convergence | `guided_retry` 只发一次指导即转 `hard_stop`，plan_mode 被提前终止 |
| 3 | **P2 — 语义降级** | context_manager | 没有 LLM 摘要；历史"压缩"仅截断最近 4 条消息 |
| 4 | **P2 — 检测盲区** | Convergence | 非失败验证（skipped/match）会重置全部历史，打断振荡识别 |
| 5 | **P2 — 内存泄漏（低概率）** | Server SSE | stream session history 无界增长，超长 plan_mode 下可积累万级条目 |
| 6 | **P3 — 上下文丢失** | context_manager | `CONVERGENCE_GUIDANCE_MESSAGE_ID` 未被 pin，可被窗口驱逐 |
| 7 | **P3 — 振荡漏检** | Convergence | 振荡检测仅覆盖严格 ABAB 四帧模式 |
| 8 | **P3 — 文件句柄泄漏** | session_manager | MCP 子进程日志文件句柄在父进程侧未关闭 |
| 9 | **P3 — 图实例竞争** | shared.py | 同一 thread_id 并发 `get_agent()` 可重复创建图实例 |

---

## 一、context_manager：提示词投影压缩

### 1.1 实际机制（不是 LLM Summarize）

`build_projected_context`（`context_manager.py:80-146`）是**纯确定性规则选择**，没有调用任何语言模型做摘要。流程如下：

1. 若 `len(state_messages) <= max_recent_messages`（默认 12），全量透传，不做任何压缩。
2. 超过 12 条时，从后向前**优先保留**：
   - 最新一条 `HumanMessage`（非 pinned）
   - 最新一条 `name` 含 `"verification"` 的 `ToolMessage`
   - 最新一个完整工具批次（AIMessage + 后续所有 ToolMessage）
   - 所有 `pinned_message_ids` 中的消息（目前硬编码为 `RENDER_VISION_MESSAGE_ID` 和 `SCENE_OBSERVE_MESSAGE_ID`）
   - 剩余配额从末尾倒序补充
3. 被省略的消息仅取**最末 4 条**做 `_compact_message_line`（截断到 180 字符），拼成一段 `SystemMessage` 插入上下文开头。

**关键问题：不是语义摘要，是截断摘要。** 被省略的历史只剩最后 4 条的 180 字截断，更早的历史对 agent 完全不可见。

### 1.2 已发现 Bug

#### [P3] `CONVERGENCE_GUIDANCE_MESSAGE_ID` 未被 pin（`shared.py:1017-1018`）

```python
# shared.py invoke_role_agent()
messages, summary_text, omitted_count = build_projected_context(
    base_messages=messages,
    state_messages=list(state["messages"]),
    pinned_message_ids={RENDER_VISION_MESSAGE_ID, SCENE_OBSERVE_MESSAGE_ID},
    max_recent_messages=12,
)
```

`quality_evaluator_node` 把收敛指导注入为 `SystemMessage(id=CONVERGENCE_GUIDANCE_MESSAGE_ID, ...)`，该 ID 没有出现在 `pinned_message_ids` 里。一旦消息超过 12 条，收敛指导很可能被窗口驱逐，agent 在最需要该指导的多轮失败循环中反而看不到它。

**修复建议：** 在 `pinned_message_ids` 中加入 `CONVERGENCE_GUIDANCE_MESSAGE_ID`。

#### [P2] 历史摘要信息量极低

`omitted_indices[-4:]` 只取最后 4 条被省略消息做摘要。对于 plan_mode 下执行 30+ 轮次的长流程，早期的工具执行结果、修复决策、阶段性验证结果全部沉没，agent 无从感知自己走过的路径。

**修复建议（中期）：** 为 `omitted_indices` 额外锚定若干"里程碑"消息（如 render 结果、质量评估结论），将其 compact 后写入 summary；或增加 LLM 摘要通道（对 token 敏感时可选 flash 级小模型）。

---

## 二、Convergence 收敛检测

### 2.1 实现机制

`evaluate_convergence`（`convergence.py:115-204`）维护一个最多 6 条的 `recent_verification_signatures` 滑动窗口，每条 signature 包含 `(todo_id, status, failure_bucket, verified_path, reason)`。

`_pattern_from_history` 识别三种模式：

| 模式 | 触发条件 | 结果 |
|------|----------|------|
| `hard_stop` | 指导重试后仍命中重复失败/振荡模式 | 立即熔断，跳 `checkpoint_finalize` |
| `repeat_loop` | 最近 3 条 signature key 完全相同且 `todo_id` 非空 | 第 1 次 → `guided_retry`；第 2 次 → `hard_stop` |
| `oscillation_loop` | 最近 4 条同 `todo_id`，failure_bucket 呈严格 ABAB | 同上 |

### 2.2 已发现 Bug

#### [P1] guided_retry 只给一次机会即转 hard_stop（`convergence.py:167-178`）

```python
if prior_interventions >= 1:
    return {
        ...
        "convergence_eval": {"status": "hard_stop", ...},
        ...
    }
```

`convergence_intervention_count` 只要 ≥ 1 就直接硬熔断。设计语义是"给过一次指导还在循环 → 停止"，但实际上：

- 指导通过 `SystemMessage` 注入，agent 可能在**同一轮**已经执行了修复动作，下一轮验证仍失败是因为网格/材质误差，而不是 agent 忽视了指导。
- 这导致 plan_mode 在遇到**任何连续 3 次相同失败**时必然永久终止，没有机会让 agent 换策略。

**修复建议：** 将阈值提升到 `prior_interventions >= 2` 或 `>= 3`，或在 guided_retry 时同时清零 `convergence_intervention_count` 以允许跨 todo 重新计数。

#### [P2] 任何非失败验证都会重置全部历史（`convergence.py:130-140`）

```python
if quality_status != "mismatch":
    return {
        "recent_verification_signatures": [],   # ← 清零
        "convergence_intervention_count": 0,    # ← 清零
        ...
    }
```

`quality_status == "skipped"` 时（无新鲜验证证据时 `quality_evaluator` 产出 `skipped`）也会触发清零。对于 turn_dispatch 为 `todo_only`（仅提交 todo 更新，不走 `tools → verify` 链）的回合，`skipped` 是正常结果，此时历史被清空意味着：agent 在 `todo_update` 轮次之间的失败记录全部丢失。

**修复建议：** `skipped` 状态应保持历史不变；只有 `match` 状态才清零（表示问题已解决）。

#### [P3] 振荡检测过于严格（`convergence.py:79-91`）

振荡检测要求最后 4 条**精确** ABAB（`a[bucket] == c[bucket]` 且 `b[bucket] == d[bucket]` 且 `a[bucket] != b[bucket]`），且 `failure_bucket` 必须在 `{"scale", "placement", "layout"}` 三者之中。

- 5 帧或 6 帧的振荡不会被识别。
- `material` bucket 振荡不会被识别。
- todo_id 在振荡中间切换一次，整体振荡也不会被识别。

**修复建议（低优先级）：** 放宽为"最近 N 条中同一 todo_id 出现至少 2 个交替的不同 bucket"。

---

## 三、SSE 长连接稳定性

### 3.1 心跳与续约机制（现状）

- **客户端心跳（`event_generator` keepalive）**：`routes_chat.py:864-867`，每 `keepalive_interval`（5~15 s）发一次 `heartbeat` SSE 事件，防止代理层因空闲断开连接。
- **运行时心跳（`_run_stream_runtime_heartbeat`）**：在 headless 模式下定期调用 `session_manager.touch_session()` + `coordinator.touch_activity()`，防止 idle sweeper 在 agent 运行期间回收进程。
- **Lease 心跳（`_run_stream_lease_heartbeat`）**：定期 `refresh_lease_if_owned()`，若检测到 ownership 丢失则触发 `session.request_stop(reason="ownership_lost")` 并向客户端推送错误。

### 3.2 已发现 Bug

#### [P1] 前端 `reader.read()` 网络异常未被捕获，resume 逻辑失效（`client.ts:323-325`）

```typescript
while (true) {
  const { value, done } = await reader.read()   // ← 网络中断时直接 throw，不是 {done:true}
  if (done) {
    streamEndedUnexpectedly = !sawTerminalEvent && !signal?.aborted
    break
  }
  ...
}
```

`ReadableStreamDefaultReader.read()` 在中途网络断连时**抛出异常**而不是返回 `{done: true}`。当前代码没有 try-catch 包围此调用：

- 异常向上抛出，`streamEndedUnexpectedly` 永远不会被设为 `true`。
- 外层 `while (!sawTerminalEvent && ...)` 的 resume 路径永远不执行。
- 客户端既不发 `X-Stream-Request-Id` 重连头，也不发 `Last-Event-ID`，stream 硬失败。

这意味着**整个 resumable stream 特性在最常见的瞬时断连场景下完全不工作**。

**修复建议：**

```typescript
while (true) {
  let chunk: ReadableStreamReadResult<Uint8Array>
  try {
    chunk = await reader.read()
  } catch {
    streamEndedUnexpectedly = !sawTerminalEvent && !signal?.aborted
    break
  }
  const { value, done } = chunk
  if (done) {
    streamEndedUnexpectedly = !sawTerminalEvent && !signal?.aborted
    break
  }
  ...
}
```

#### [P2] Server 端 stream session history 无上界（`routes_chat.py:92-99`）

```python
def publish(self, payload: dict[str, Any]) -> None:
    with self.lock:
        ...
        self.history.append(event_payload)   # ← 每次都追加，无 cap
```

每个 `delta`（token 流）、`graph_node` 事件、`tool` 事件都追加到 `history`。对于一个 plan_mode 请求（30 min 执行、数千 token delta + 数十 graph_node 事件），`history` 可能累积 **5,000–20,000 条**字典对象，在 `mark_done()` 后仍保留 120 秒。

- 当前项目无高并发需求，单个超长会话的内存峰值大约在数十 MB 级，**短期无崩溃风险**。
- 但若将来并发请求增多或 plan_mode 执行时间更长，此处会成为显著内存瓶颈。

**修复建议（中低优先级）：** 设置 history 最大长度（如 2,000 条），超出后丢弃最旧的 non-resumable 条目（仅保留尾部 N 条用于客户端 replay）。由于 resume 客户端只需从 `Last-Event-ID` 之后取事件，更老的条目可以安全丢弃。

### 3.3 多进程 / 单 worker 下的进程清理稳定性

**现状：** `run_api()` 内已有硬约束 `worker_count = 1`（`shared.py:2237-2252`），并在日志中提示"use multiple processes on different ports with unique `API_WORKER_ADVERTISE_URL`"。这是正确的多进程模型：每个 uvicorn 进程独立运行，通过 Redis 做 lease 协调和 port registry。

#### 进程独立状态不共享（设计正确，需注意）

| 状态 | 位置 | 作用域 |
|------|------|--------|
| `_ACTIVE_STREAM_SESSIONS` | `routes_chat.py:57` | 进程内 dict，**不跨 worker** |
| `_agent_graphs_by_thread` | `shared.py:82` | 进程内 dict，**不跨 worker** |
| `_idle_sweeper_task` | `shared.py:83` | 进程内 asyncio Task |
| `_sessions` | `session_manager.py:82` | 进程内 dict，**不跨 worker** |

SSE stream session 创建在 Worker A 后，若 Worker B 收到续传请求（带 `X-Stream-Request-Id`），`_get_stream_session` 返回 `None` → 409。但 `claim_or_proxy_request` 会将请求代理回 Worker A，因此**正常情况下 resume 仍能路由到正确 worker**。

#### [P3] Idle sweeper 在进程内重复启动的防御（已有保护）

```python
if _idle_sweeper_task is None or _idle_sweeper_task.done():
    _idle_sweeper_task = asyncio.create_task(_idle_session_sweeper())
```

`startup_event` 有 `done()` 保护，防止重复创建。在 `shutdown_event` 中也有取消逻辑，结构正确。

#### [P3] MCP 日志文件句柄泄漏（`session_manager.py:657-689`）

```python
log_file = open(log_path, "w", buffering=1)
...
session.mcp_process = subprocess.Popen(
    [command, *args],
    stdout=log_file,
    stderr=subprocess.STDOUT,
    ...
)
# ← log_file 未被显式 close()，在父进程侧持续占用 fd
```

`log_file` 作为子进程的 stdout/stderr 后，父进程侧的 fd 应该关闭（子进程自己持有一个 fd 引用）。目前父进程持续持有这个 fd，在 `terminate_session_processes` 终止子进程后该 fd 变为死句柄，仍占用文件描述符直到 Python GC。对于频繁重启的 headless session（如因空闲回收再重建），fd 会缓慢泄漏。

**修复建议：** 在 `subprocess.Popen` 成功后调用 `log_file.close()`（子进程已继承 fd，不影响其写入）。

#### [P3] `get_agent()` 并发调用可重复创建图实例（`shared.py:1020-1034`）

```python
if graph_mismatch:
    previous_graph = graph
    next_graph = await _create_agent_graph_for_runtime(...)   # ← await 点，事件循环可调度其他协程
    ...
    _agent_graphs_by_thread[thread_id] = next_graph           # ← 写入
```

若两个请求几乎同时对同一 `thread_id` 调用 `get_agent()`，均在 `graph_mismatch = True` 时进入，各自 `await` 创建图，最终后写的覆盖先写的。先创建的图对象未被记录，其 MCP 连接引用也不会被释放。在 headless 模式下这可能导致 MCP 连接孤儿。

**注意：** 当前 `worker_count = 1` 且 asyncio 单事件循环下，此场景需要两个用户请求**确实并发**到达，实际触发概率极低。

---

## 四、修复优先级建议

### 立即修复（不涉及架构改动）

1. **[P1] 前端 `reader.read()` 加 try-catch** — 5 行改动，修复整个 resume 特性的核心缺陷。
2. **[P3] `CONVERGENCE_GUIDANCE_MESSAGE_ID` 加入 `pinned_message_ids`** — 1 行改动，防止指导消息被驱逐。

### 短期修复（本迭代内）

3. **[P1] 提高 convergence guided_retry 阈值** — 将 `prior_interventions >= 1` 改为 `>= 2` 或 `>= 3`，减少 plan_mode 过早熔断。
4. **[P2] convergence 历史在 `skipped` 时不清零** — 仅在 `match` 时清零，保持振荡识别的连续性。
5. **[P3] MCP log_file 在 Popen 后显式关闭** — 1 行改动，防止 fd 泄漏。

### 中期改进（下次迭代）

6. **[P2] Server SSE history 加 cap** — 设置 `history` 最大长度（如 2,000），超出时丢弃可不重放的旧条目。当前项目规模下非紧急，但应在引入更多并发 plan_mode 用户前完成。
7. **[P2] context_manager 摘要质量提升** — 为被省略的历史增加"里程碑"锚定（质量评估、render 结果），或引入可选的 LLM flash-summary 通道。

---

*生成时间：2026-03-01 | 代码基准：dev branch，commit 见 git log*
