# Local-Client Verify 参考切换分析记录

更新时间：2026-02-22

## 背景

本记录用于沉淀以下问题的分析结论：

1. 是否可以在 `local-client` 模式下，用更轻量的 `get_viewport_screenshot` 替代 `scene_observe`，作为 `verify` 节点输入参考。
2. 若要实现“动态切换”，应改图结构还是改节点内策略。

---

## 当前实现关键点（代码定位）

1. 图主链路固定为：
   - `tools -> update_memory -> scene_observe -> verify`
   - 位置：`/Users/fishwowater/projects/3DSceneAgent/scene_agent/agent/graph.py`
2. `verify_node` 的主要触发条件是 `last_render_path`（并对 `last_verified_path` 去重）：
   - 位置：`/Users/fishwowater/projects/3DSceneAgent/scene_agent/agent/nodes.py`
3. `scene_observe_node` 当前在场景变更后调用 `update_scene_cameras`，内部通过 `camera_observe` 生成多视角结果：
   - 位置：`/Users/fishwowater/projects/3DSceneAgent/scene_agent/agent/nodes.py`
   - 位置：`/Users/fishwowater/projects/3DSceneAgent/mcp_server/tools/multimodal/camera_tools.py`
4. 当前 MCP tool 门控只影响“LLM可调用工具列表”，不影响 `scene_observe_node` 这种内部直接命令路径。

---

## 可行性结论

可行。因为 `verify_node` 不强依赖 `scene_observe` 本身，只依赖可解析的 `last_render_path`。

只要上游节点能产出：

1. `last_render_path`
2. 可选的 `last_render_source`
3.（可选）供 VLM 使用的图片消息

就可以被 `verify` 正常消费。

---

## 推荐改造路线（当需要落地时）

优先做“节点内策略切换”，而不是“动态改图”：

1. 保持图拓扑不变（仍保留 `scene_observe -> verify`）。
2. 在 `scene_observe_node` 内按运行模式/可用工具分支：
   - `local-client`：优先走 `get_viewport_screenshot`
   - `headless`：保留 `update_scene_cameras` 逻辑
3. 失败时维持现有防呆语义：返回 `{"last_render_path": None}`，避免误验证旧图。

该方式对现有路由、副作用和状态字段影响最小。

---

## 工作量预估（此前讨论结论）

1. 小改方案（节点内策略切换 + 单测）：约 0.5 天。
2. 动态改图方案（按模式重配 nodes/edges）：约 1-2 天，且需要更多回归验证。

---

## 相关门控更新（本次已执行）

根据当前需求，已将 `undo_last_snapshot` 调整为仅 `BLENDER_MODE=headless` 可注册：

1. 修改：`/Users/fishwowater/projects/3DSceneAgent/mcp_server/tool_registry.py`
2. 单测更新：`/Users/fishwowater/projects/3DSceneAgent/tests/unit/test_tool_registry.py`
   - `local-client` 断言不包含 `undo_last_snapshot`
   - `headless` 断言包含 `undo_last_snapshot`
