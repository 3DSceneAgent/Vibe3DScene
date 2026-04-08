# 前端 telemetry / grid / stop 排查与实施计划

日期：2026-04-06

相关代码路径：
- `scene_agent/agent/nodes/evaluators.py`
- `scene_agent/agent/todo_state.py`
- `scene_agent/interfaces/api/routes_chat.py`
- `scene_agent/interfaces/api/shared.py`
- `scene_agent/vlm/providers.py`
- `scene_agent/vlm/verification.py`
- `mcp_server/tools/multimodal/camera_tools.py`
- `web/src/App.tsx`
- `web/src/api/client.ts`
- `web/src/components/TodoPanel.tsx`
- `web/src/components/ChatTab.tsx`

---

## 1. 本次范围

本次仅对四个问题做结论与计划沉淀，其中：

- `todo` 问题只回答现状，不进入改造范围。
- `grid overview` 进入改造计划。
- `context length / token used / tool calls` 是最高优先级，进入正式设计。
- 前端停止输出链路进入排查与修复计划。

---

## 2. 直接结论

### 2.1 Todo 生命周期现状

`plan mode` 下从 `todo1` 演进到 `todo2` 时，`todo1` 不是“没被标记完成”，而是由 `evaluator_node` 在验证结果为 `done` 的那一轮里直接调用 `apply_todo_actions()`：

- 将当前 `active_todo_id` 标记为 `completed`
- 基于最新 todo 投影重新计算 `active_todo_id`
- 让下一个 open todo 成为新的 active todo

因此，后端语义上 `todo1 -> completed` 与 `todo2 -> active` 是同一轮状态推进。

当前“看起来像直接跳到 todo2 marked as working”的主要原因在前端展示：

- `TodoPanel` 在流式阶段会把 active 的 `pending` todo 直接显示成 `in_progress`
- 折叠态通常只展示当前项

所以用户更像是在看“当前活跃项”，而不是完整的 todo 状态转移轨迹。

本项不改代码，只保留以上结论。

### 2.2 Grid overview 现状

`Grid overview` 不是前端把 3 张图排成 2x2，而是后端先在 `camera_tools.py` 里把多张渲染图拼成一张 overview 图片。

当前逻辑：

- 图数 `<= 4` 时固定 `2` 列
- 3 张图因此会生成 `2x2` 画布，右下角留空

所以 1x3 改造应落在后端拼图逻辑，而不是前端消息排版。

### 2.3 Token / context / tool-call 统计现状

当前系统没有形成产品级的 telemetry 能力。

已有基础：

- 部分 LangChain message 对象本身可能携带 `usage_metadata`
- LangChain message 序列化层会保留 `usage_metadata`
- `Gemini` / `Qwen` provider 本地依赖均支持 usage 相关能力
- `Gemini` provider 本地依赖支持官方 `count_tokens`

现状缺口：

- `invoke_role_agent()`、`verify_render_with_references()`、reference helper、context summary helper 都没有统一提取 usage
- 结构化输出路径经常只留下解析后的对象，usage 没有被转成 telemetry 记录
- 没有统一采集所有内部 VLM 调用
- 没有按 `turn_id` 聚合
- 没有 thread 级累计
- 没有 graph state 中的 telemetry source-of-truth 字段
- 没有前端类型与 UI
- 没有把图片 token、tool calls、context used/limit 贯穿到历史接口

补充说明：

- 这里的核心问题不是“message 对象一定已经丢失 usage_metadata”，而是“系统没有把 usage_metadata 变成可聚合、可持久化、可对外展示的 telemetry 能力”。
- 当前 `HistoryMessageResponse` 也没有 usage 维度，因此即便某些 message 对象内部带着 usage，前端历史接口依然拿不到可直接使用的统计值。

### 2.4 停止按钮现状

前端当前的“停止”本质上是本地 `AbortController.abort()`，用于断开当前 SSE 连接。

后端虽然有 `session.request_stop()` 能力，但目前没有给前端暴露正式 stop API。结果是：

- 第一次点击可能只断开前端流
- 后端执行仍继续
- 前端又有 `stream-session / resume` 恢复逻辑
- 因此可能出现“第一次没停，第二次才像是真的停了”的体验

---

## 3. 哪些需要持久化

这里的“持久化”指：跨当前 SSE 连接、跨页面刷新、跨 thread 历史读取仍能得到一致结果，而不是只存在于内存态。

### 3.1 明确需要持久化的内容

只有第（3）项必须新增持久化设计。

原因：

- 需求明确要求统计“所有历史 turn”
- 还要求统计内部 VLM 调用，不只是最终 assistant 文本
- 单靠当前 SSE progress 或前端内存态无法满足
- 单靠当前消息历史也不够，因为 router / planner / verifier parser / helper 等内部调用并不稳定地映射为历史消息

建议持久化对象：

1. `llm_call_records`
- 作为 append-only 记录写入 graph checkpoint state
- 每次模型调用写一条
- 作为 thread 历史统计的唯一事实来源
- 需要在 `scene_agent/agent/state.py` 中正式定义字段，而不是只停留在文档建议

建议字段：

- `call_id`
- `turn_id`
- `thread_id`
- `node_name`
- `call_role`
  - 例如 `router` / `planner` / `agent` / `builder` / `verifier` / `verification` / `helper`
- `provider`
- `model`
- `input_tokens`
- `output_tokens`
- `total_tokens`
- `image_input_tokens`
  - 仅在能拿到官方精确值时写入
- `context_limit_tokens`
  - 仅在能精确确定时写入
- `created_at_ms`

建议状态定义：

- 在 `scene_agent/agent/state.py` 新增 `LLMCallRecord` TypedDict
- 新增 list reducer，例如 `append_llm_call_records(existing, new)`
- 在 `AgentState` 中新增：
  - `llm_call_records: NotRequired[Annotated[list[LLMCallRecord], append_llm_call_records]]`

原因：

- 现在 `AgentState` 中不存在任何可承接 telemetry 明细的字段
- 如果不先定义 reducer，多个节点返回 patch 时无法稳定地做 append-only 聚合
- 这一步是第（3）项持久化设计的必要前置

2. `turn_metrics`
- 可以不单独持久化为 source-of-truth
- 由 `llm_call_records` 与 assistant tool-calls 在读取历史时聚合生成
- 若后续性能需要，可作为派生缓存持久化

3. `thread_metrics`
- 同样建议作为派生聚合，不直接作为 source-of-truth
- 读取 thread history 时基于 `llm_call_records` 计算

### 3.2 明确不需要新增持久化的内容

第（2）项 `grid overview` 不需要新增持久化。

- 这是纯展示生成逻辑
- 改后端拼图布局即可
- 生成出来的图片仍沿用现有 renders 保存机制

第（4）项 `stop` 问题在本轮修复中不需要新增 durable persistence。

- 只要给前端暴露 stop API，让后端当前 active stream session 进入 `stop_requested`
- 并阻断前端自动 resume 旧流
- 就足够修复“双击才停”的问题

补充说明：

- 如果未来要做“刷新后继续可控地 cancel / resume 正在运行的任务”，那就应该上更完整的 persisted run model
- 但这不是本次 stop bug 修复的必需条件

第（1）项 `todo` 在当前范围内不改，因此无新增持久化要求。

---

## 4. 第（3）项正式设计

### 4.1 产品口径

按当前决策，统计口径采用：

- 全链路统计
  - 同一 `turn_id` 触发的所有内部 VLM 调用都计入
- UI 采用“线程头部累计 + 每轮 turn 明细”
- 只显示精确值
  - 拿不到 provider 官方精确值时显示 `unknown` / `null`
  - 不做近似估算

### 4.2 为什么不能只依赖现有 message history

当前 `HistoryMessageResponse` 不包含完整 usage 维度，也不足以覆盖所有内部调用：

- `router_node` 的结构化路由调用不保证进入历史消息
- `plan_node` 的结构化规划调用不保证进入历史消息
- `verify_render_with_references()` 内部验证调用不会自动保留 usage
- `verifier_feedback` 的 parser 调用不保证进入历史消息
- reference helper / context summary helper 也是内部调用

因此必须有一层独立的、与 message history 解耦的 usage 记录。

### 4.3 采集点

建议建立统一 usage capture wrapper，并覆盖以下入口。

但这里要明确一条职责边界：

- wrapper 负责“调用模型 + 提取 usage + 生成标准化 `LLMCallRecord`”
- graph node 负责“把 `LLMCallRecord` 追加写入 state patch”
- wrapper 不直接写 `AgentState`

原因：

- `scene_agent/vlm/` 层不应该直接依赖 graph state 写入语义
- 当前只有 graph node 的返回 patch 会自然进入 checkpoint
- helper / verification / context summary 这些深层函数如果直接碰 state，会让耦合快速失控

建议新增模块：

- `scene_agent/vlm/metrics.py`

建议最小能力：

1. `invoke_with_metrics(...)`
- 处理普通 `invoke()`
- 返回：
  - `response`
  - `llm_call_record | None`

2. `invoke_structured_with_metrics(...)`
- 处理 `with_structured_output(...)` 路径
- 目标不是只拿解析后的对象，而是尽量同时拿到：
  - `parsed`
  - `raw_response`
  - `llm_call_record | None`

3. `resolve_image_input_tokens(...)`
- provider 级图片 token 精确值策略封装

4. `resolve_context_limit_tokens(...)`
- provider/model 级 context limit 精确值读取封装

5. `build_llm_call_record(...)`
- 统一构造字段：
  - `call_id`
  - `thread_id`
  - `turn_id`
  - `node_name`
  - `call_role`
  - `provider`
  - `model`
  - `input_tokens`
  - `output_tokens`
  - `total_tokens`
  - `image_input_tokens`
  - `context_limit_tokens`
  - `created_at_ms`

具体写入点建议如下：

1. 主 agent 调用
- `invoke_role_agent()`
- 覆盖 `general / builder / verifier`
- 这里直接把 wrapper 返回的 `llm_call_record` 写入返回 patch：
  - `{"messages": [response], "llm_call_records": [record]}`

2. 轻量结构化节点
- `router_node()`
- `plan_node()`
- `verifier_feedback_node()` 内 parser 调用
- 这些函数本身就是 graph node，适合各自追加 `llm_call_records`

3. verification 调用
- `verify_render_with_references()`
- 不建议在这里直接写 state
- 建议改为返回：
  - `verification_payload`
  - `llm_call_records`
- 再由 `verify_node()` 负责把记录写入 state patch

4. helper 调用
- reference image helper
- context summary helper
- 这类函数当前返回 tuple / 文本，不能直接写 checkpoint
- 建议把返回值扩展为：
  - `业务结果`
  - `llm_call_records`
- 再由最近的 graph node 负责合并写入

特别注意：

- `context summary helper` 的最近写入点不是 `context_manager.py` 本身，而是调用它的 `invoke_role_agent()`
- reference image helper 的最近写入点通常是引用它们的 shared node 路径，而不是 helper 自己
- 这能避免“文档里说有统一 wrapper，结果真正写 state 的地方没人负责”的问题

### 4.4 Provider 精确值策略

#### Gemini

本地依赖与官方能力确认如下：

- `langchain-google-genai 4.2.0`
- `google-genai 1.60.0`
- 响应 `usage_metadata` 可拿到精确 `input/output/total`
- 官方支持 `models.countTokens`

因此 Gemini 策略为：

- 基础输入/输出/总 token：直接使用响应 `usage_metadata`
- 图片 token：
  - 若响应本身提供精确细分，则直接使用
  - 若未提供精确图片细分，则对“完整 multimodal 输入”调用一次 `countTokens`
  - 对“去掉图片后的文本输入”再调用一次 `countTokens`
  - 二者差值作为精确 `image_input_tokens`
- context limit：
  - 优先使用模型 `profile.max_input_tokens`
- structured output 路径：
  - wrapper 需要尽量拿到 raw response 对应的 usage
  - 如果 `with_structured_output()` 只返回解析对象而拿不到 raw usage，则该路径不能假定“天然可统计”
  - 实现时要么使用支持 raw 返回的 structured 模式，要么退回“先取 raw，再解析”

#### Qwen

本地依赖与文档能力确认如下：

- `langchain-qwq 0.3.4`
- 流式路径开启了 `stream_options.include_usage = True`
- LangChain 文档示例确认 `ChatQwen` 会返回 `usage_metadata`
- DashScope / Model Studio 文档确认多模态响应可能包含 `usage.image_tokens`

因此 Qwen 策略为：

- 基础输入/输出/总 token：直接使用响应 `usage_metadata`
- 图片 token：
  - 仅当 provider 原始 usage 中能明确拿到精确 `image_tokens` 时才写入
  - 若 LangChain 层已经丢失该字段，则本轮显示 `null`
  - 不使用图片尺寸公式做反推
- context limit：
  - 仅在能从 provider/profile 或稳定官方模型映射中精确确定时写入
  - 否则显示 `unknown`

### 4.5 图片 token 精确值定义

需要把“差值计算逻辑”明确成统一规则，而不是在实现时临时决定。

定义如下：

1. Gemini
- `image_input_tokens = count(multimodal_full_input) - count(text_only_input)`
- 两次计数都必须基于同一轮调用的最终 prompt 内容
- 文本版输入只移除图片内容块，不改动其余文本、system prompt、顺序与消息边界
- 若任一官方计数失败，则该字段写 `null`，不回退到估算

2. Qwen
- 仅接受 provider 明确返回的精确图片 token 字段
- 如果 LangChain 或 provider 响应里拿不到该字段，则写 `null`
- 不做二次 tokenizer 估算，也不做图片尺寸/patch 公式推导

### 4.6 结构化输出路径的特殊处理

review 提醒得对，现有很多调用点都走了 `with_structured_output()`，而这条路径最容易把 usage 藏起来。

因此计划中必须把它单独列成设计约束：

- wrapper 不能只返回 `parsed_result`
- wrapper 需要优先保留 raw response 侧的 usage 信息
- 对于 `router_node()`、`plan_node()`、`verifier_feedback_node()`、reference helpers、`verify_render_with_references()` 的 structured path，都要走同一套处理逻辑

如果当前 provider / LangChain 版本做不到“structured result + raw usage 同时可得”，则落地策略应为：

1. 先走 raw invoke
2. 从 raw response 提取 usage
3. 再在本地完成 schema parse / model_validate

这条约束必须写进实现说明，否则第（3）项会在 structured 路径上出现系统性漏记。

### 4.7 Tool calls 统计

tool calls 的事实来源不必单独持久化新表，可以按以下方式处理：

- 历史回放场景：
  - 基于 checkpoint 中 assistant message 的 `tool_calls` / `additional_kwargs.tool_calls`
  - 按 `tool_call id/key` 去重
  - 聚合到对应 `turn_id`
- 流式进度场景：
  - 继续使用现有 `tool_call_started` 事件做实时展示

如后续发现历史聚合成本过高，再考虑将每 turn 的 `tool_call_count` 派生缓存进 graph state。

### 4.8 API 与前端展示

建议改动：

1. `ThreadHistoryResponse`
- 新增 `thread_metrics`
- 新增 `turn_metrics_by_turn_id`

2. `ThreadStreamSessionResponse.progress`
- 新增 live metrics 字段：
  - `llm_input_tokens`
  - `llm_output_tokens`
  - `llm_total_tokens`
  - `image_input_tokens`
  - `tool_calls_started`
  - `peak_context_used_tokens`
  - `peak_context_limit_tokens`

3. 前端展示
- thread 顶部显示累计：
  - input
  - output
  - total
  - tool calls
  - peak context used / limit
- 每个 conversation turn 显示本轮聚合值
- 如字段为 `null/unknown`，明确显示为 `unknown`

补充：

- `HistoryMessageResponse` 现状不承载 telemetry，因此本轮不建议把 usage 零散塞回每条消息 DTO
- 更合适的是在 thread / turn 粒度提供聚合结果，把 message history 和 telemetry history 解耦

---

## 5. 第（2）项改造计划

### 5.1 目标

把 3 视图 `Grid overview` 从当前视觉上的 `2x2` 留空布局改成 `1x3`。

### 5.2 实施方式

仅修改后端拼图逻辑：

- `image_count == 2`：`1x2`
- `image_count == 3`：`1x3`
- `image_count == 4`：`2x2`
- `image_count > 4`：保留当前多列策略

前端不做特殊适配，因为前端展示的是拼接后的单张 overview 图。

### 5.3 持久化要求

无新增持久化要求。

---

## 6. 第（4）项改造计划

### 6.1 根因

当前 stop 行为只中断前端连接，不保证终止后端执行。

### 6.2 最小修复方案

新增后端 stop 接口，例如：

- `POST /threads/{thread_id}/stream-session/stop`

建议路由位置：

- 与现有 `GET /threads/{thread_id}/stream-session` 放在同一个 `routes_chat.py` router 下
- 直接复用：
  - `resolve_frontend_client_id()`
  - `ensure_frontend_client_can_manage_thread()`
  - `_find_thread_stream_session()`

建议请求参数：

- `stream_request_id: string | null`

建议返回字段：

- `thread_id`
- `stream_request_id`
- `accepted`
- `already_requested`
- `done`

行为：

- 定位当前 thread 的 active stream session
- 调用 `request_stop(reason="user_stop")`
- 返回 stop 已接受
- 如果请求带了 `stream_request_id` 且与当前 active session 不匹配，返回 `409`
  - 目的是避免前端旧页面或旧请求误停新一轮 stream

前端 `handleStop()` 改为：

1. 先请求 stop API
2. 再 `abort()` 当前 SSE
3. 清理本地 streaming placeholder
4. 在本轮 stop 尚未确认 terminal 前，阻止自动 resume 旧流

前端还需要新增本地保护：

- `stopRequestedStreamRequestId`
- 只要这个值匹配当前流且尚未收到 `done`，`restore/resume` 逻辑都不能自动重连
- 收到 terminal `done/error` 后再清空该保护位

### 6.3 持久化要求

本轮 stop 修复不需要新增 durable persistence。

依赖现有：

- `_ActiveStreamSession.stop_requested`
- 现有 stream-session 查询能力

即可完成修复。

---

## 7. 测试计划

### 7.1 Todo 现状验证

- 验证 `evaluator_node` 在 `verification_result.status == done` 时：
  - 当前 todo 被标记为 `completed`
  - `active_todo_id` 自动推进
- 本项只做回归验证，不做代码改动

### 7.2 Grid overview

- 2 图生成 `1x2`
- 3 图生成 `1x3`
- 4 图保持 `2x2`

### 7.3 Telemetry

- Gemini 文本 turn：精确 input/output/total
- Gemini 图片 turn：精确 image_input_tokens
- Qwen 文本 turn：精确 input/output/total
- Qwen 图片 turn：
  - 若 provider usage 暴露 image_tokens，则前端可见
  - 若未暴露，则显示 `null/unknown`
- thread 总计等于所有历史 turn 之和
- router / planner / agent / verifier / helper 调用都能落到同一 turn 汇总中
- structured output 路径不会因为只返回解析对象而漏记 usage
- context summary helper 的调用也会被计入对应 turn
- `AgentState.llm_call_records` 在 checkpoint 中可回放，并可稳定重建 turn/thread 聚合

### 7.4 Stop

- 单击一次 stop 即可让后端 session 进入 stop path
- stop 后不会被前端自动 resume 逻辑重新接回
- stop 后可正常开启新请求

---

## 8. 外部依据

用于第（3）项可行性判断的主要资料：

- LangChain Python `ChatGoogleGenerativeAI` 集成文档  
  https://docs.langchain.com/oss/python/integrations/chat/google_generative_ai

- Gemini 官方 `models.countTokens` 文档  
  https://ai.google.dev/api/tokens

- LangChain Python `ChatQwen` 集成文档  
  https://docs.langchain.com/oss/python/integrations/chat/qwen

- Alibaba Cloud Model Studio Qwen API 文档  
  https://www.alibabacloud.com/help/en/model-studio/use-qwen-by-calling-api

- Alibaba Cloud Model Studio 视觉理解文档  
  https://www.alibabacloud.com/help/en/model-studio/vision/

- Alibaba Cloud Model Studio 模型列表  
  https://www.alibabacloud.com/help/en/model-studio/models

本地依赖版本核对结果：

- `langchain-google-genai 4.2.0`
- `google-genai 1.60.0`
- `langchain-qwq 0.3.4`
- `langchain-core 1.2.7`
- `langgraph 1.0.7`

---

## 9. 建议执行顺序

1. 先做第（3）项 telemetry 的后端持久化与 history API
2. 再做前端头部与 turn 明细展示
3. 并行做第（2）项 `Grid overview` 1x3
4. 最后修第（4）项 stop API 与前端 stop/resume 协调

其中第（3）项是唯一明确必须引入新增持久化设计的工作项。
