# 图片资产与工作流状态补齐实施记录

更新时间：2026-02-26  
作者：Codex

---

## 1. 背景与目标

本次实现用于补齐此前重构中尚未闭环的部分，重点是：

1. 将 `reference_image` 从“单一验证图”扩展为“可绑定任务语义的图片资产系统”。
2. 打通后端接口：上传资产、按任务/角色解析、绑定管理。
3. 让 agent 工作流在 `task_mode + task_id` 下按语义读取图片，而不是仅按“最近上传”。
4. 保留 legacy `/reference-images` 行为，确保现有调用路径仍可用。

参考依据（仓库内）：

1. `docs/codex_created/agentic-workflow-refactor-implementation-2026-02-25.md`
2. `docs/codex_created/agentic-workflow-abstraction-and-planmode-dual-agent-plan-2026-02-26.md`
3. 现有 `tests/unit|contract|integration` 行为约束

---

## 2. 实施范围

### 2.1 内存与存储层

涉及文件：

1. `scene_agent/memory/reference_image_store.py`
2. `scene_agent/memory/reference_image_memory.py`
3. `scene_agent/memory/__init__.py`

关键结果：

1. 引入统一图片资产模型 `ImageAsset` 与任务绑定模型 `ImageBinding`。
2. Redis/in-memory 双栈支持：
   - 资产：`img:{thread}:order` + `img:{thread}:meta:{id}`
   - 绑定：`imgbind:{thread}:{task}:order` + `imgbind:{thread}:{task}:meta:{id}`
3. 支持核心能力：
   - `add_assets`, `list_assets`
   - `bind_images`, `list_bindings`
   - `resolve_assets(thread_id, task_id, roles, limit)`
4. legacy 兼容：
   - `add_images/list_images` 仍可用
   - legacy 上传自动绑定到 `GLOBAL_TASK_ID + verification_reference`

### 2.2 Agent 状态与节点

涉及文件：

1. `scene_agent/agent/state.py`
2. `scene_agent/agent/nodes.py`

关键结果：

1. `AgentState` 新增 `task_id`（短期记忆中的任务作用域键）。
2. `route_mode_node` 会在请求进入时确定默认 `task_id`（conversation/single_action/plan）。
3. `verify_node` 改为通过 `resolve_assets` 按 `task_mode` 角色过滤读取参考图：
   - `conversation_mode`: question/style/verification
   - `single_action_mode`: object/style/verification
   - `plan_mode`: scene/object/style/verification
4. 为测试与扩展保留 alias：`get_reference_image_memory = get_image_asset_memory`。

### 2.3 API 层

涉及文件：

1. `scene_agent/interfaces/api.py`

关键结果：

1. `ChatRequest` 新增 `task_id`，并透传到 `/chat` 与 `/chat/stream` agent 输入状态。
2. 新增图片资产接口：
   - `POST /threads/{thread_id}/images`
   - `GET /threads/{thread_id}/images`
   - `POST /threads/{thread_id}/tasks/{task_id}/image-bindings`
   - `GET /threads/{thread_id}/tasks/{task_id}/image-bindings`
3. `/reference-images` 保留为 legacy 接口，内部切到新内存模型。
4. multipart 上传请求改为当前 worker 处理（避免 owner-proxy 转发流重复消费）。
5. 线程清理增强：删除 `image_assets` 与（如可用）`graph_checkpoints`。

---

## 3. 测试补齐与稳定性修正

涉及文件：

1. `tests/contract/test_reference_images.py`
2. `tests/integration/test_reference_images.py`
3. `tests/unit/test_example_prompts_api.py`

新增/调整：

1. contract 新增 `/images + image-bindings` 覆盖用例。
2. integration 新增“task 绑定 + global 绑定联合作用域解析”用例。
3. unit 中 API 示例测试将固定 thread_id 改为唯一 thread_id（`uuid4`），避免 Redis owner 租约残留导致偶发 503。

---

## 4. 验证记录

执行命令：

1. 语法检查
```bash
python -m py_compile \
  scene_agent/memory/reference_image_store.py \
  scene_agent/memory/reference_image_memory.py \
  scene_agent/interfaces/api.py \
  scene_agent/agent/nodes.py \
  scene_agent/agent/state.py \
  scene_agent/memory/__init__.py
```
结果：通过。

2. 单元测试
```bash
pytest -q tests/unit
```
结果：`194 passed, 4 skipped`。

3. 合同 + 集成（图片相关）
```bash
pytest -q tests/contract/test_reference_images.py tests/integration/test_reference_images.py
```
结果：`4 passed`。

---

## 5. 最终效果总结

1. 参考图像逻辑已从“单用途验证图”升级为“资产 + 语义绑定”模型。
2. agent 可按任务阶段和意图读取不同角色图片，支持更细粒度 workflow 扩展。
3. 旧接口仍可用，前后端可渐进迁移。
4. 测试已覆盖关键新接口与解析路径，整体回归通过。

