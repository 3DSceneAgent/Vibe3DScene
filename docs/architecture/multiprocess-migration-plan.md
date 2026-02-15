# 3D Scene Agent 多进程迁移计划（Redis 控制面 + 多 Worker）

## Summary
把当前进程内会话状态迁移为 Redis 控制面，支持多个 API 进程并发服务同一系统，保证同一 `thread_id` 全局单 owner、可接管、可恢复，并保持现有 HTTP/SSE 接口语义不变。  
本次按一次切换执行，`/ws` 直接删除，支持范围为 `BLENDER_MODE=headless`。

## 已确认决策
1. 仅支持 `headless` 的多 worker；`local-client` 继续单 worker。
2. 发布方式为一次切换（不做双轨 feature flag）。
3. 删除后端 `WS /ws`（前端未使用）。
4. 单机场景采用“多进程多端口（每进程 1 worker）”，不是 `uvicorn --workers N`。
5. 路由模型采用文档方案：owner-proxy（非 owner 转发给 owner）。

## 公共接口与行为变更
1. 保持兼容接口：`POST /chat`、`POST /chat/stream`、`GET /scene/{thread_id}`、`GET /scene/{thread_id}/renders`、`GET /scene/{thread_id}/gltf`、`GET /scene/{thread_id}/blend`、`GET /todos/{thread_id}`、`GET/POST /threads/{thread_id}/reference-images`、`GET /threads/{thread_id}/mcp-tools`、`GET /vlm/models`。
2. 删除接口：`WS /ws`，并从根路由文档中移除。
3. 新增响应头（可选）：`X-Session-Owner`、`X-Session-Lease-Epoch`。
4. 扩展 `GET /health`：增加 `worker_id`、`redis_ok`、`redis_latency_ms`。
5. `GET /threads` 从 Redis 返回真实线程列表（不再返回占位 TODO）。

## 配置与运行约束
1. 在 `/Users/fishwowater/projects/3DSceneAgent/scene_agent/config.py` 增加配置：`REDIS_URL`、`REDIS_KEY_PREFIX`、`SESSION_LEASE_TTL_SECONDS`、`SESSION_HEARTBEAT_INTERVAL_SECONDS`、`SESSION_OWNER_UNREACHABLE_GRACE_SECONDS`、`API_WORKER_ID`、`API_WORKER_ADVERTISE_URL`、`SESSION_SHARED_STORAGE_ROOT`。
2. 兼容旧变量：`SESSION_BLEND_ROOT` 作为 `SESSION_SHARED_STORAGE_ROOT` 的别名读取。
3. 在 `/Users/fishwowater/projects/3DSceneAgent/requirements.txt` 增加 `redis` 依赖。
4. 在 `/Users/fishwowater/projects/3DSceneAgent/scene_agent/interfaces/api.py` 的 `run_api()` 中强制单进程单 worker运行（`workers=1`），并在日志中明确“多 worker 通过多进程多端口部署”。

## Redis 数据模型（按文档落地）
1. `sa:{session:<sid>}:meta`（HASH）：owner、owner_url、lease_epoch、status、last_active_ms、host、blender_port、mcp_port、storage_dir、blend_path、snapshot_dir、updated_at_ms。
2. `sa:{session:<sid>}:lease`（STRING+TTL）：`<worker_id>:<epoch>:<uuid>`。
3. `sa:{session:<sid>}:fence`（INCR）：fencing epoch。
4. `sa:sessions:last_active`（ZSET）：线程活跃索引。
5. `sa:worker:<worker_id>:sessions`（SET）与 `sa:workers`（SET）：owner索引。
6. 端口占用：`sa:ports:<host>:headless`、`sa:ports:<host>:mcp`（SET）。
7. 参考图元数据：`sa:ref:<thread_id>:order`（ZSET）、`sa:ref:<thread_id>:meta:<image_id>`（HASH）。
8. Checkpoint：`sa:ckpt:<thread_id>:<ns>:index`、`sa:ckpt:<thread_id>:<ns>:<checkpoint_id>`、`sa:ckpt_blob:<thread_id>:<ns>:<channel>:<version>`、`sa:writes:<thread_id>:<ns>:<checkpoint_id>`。

## 代码实施清单（决策完成）
1. 新增 `/Users/fishwowater/projects/3DSceneAgent/scene_agent/session/redis_registry.py`。  
实现 Lua/CAS 原子操作：claim、renew、force-claim、touch-active、register/unregister-worker、port reserve/release。
2. 新增 `/Users/fishwowater/projects/3DSceneAgent/scene_agent/session/session_coordinator.py`。  
提供 `claim_or_get_owner(thread_id)`、`renew_lease(...)`、`release_if_owned(...)`、`list_threads()`、`touch_activity(...)`、`get_owner(...)`。
3. 新增 `/Users/fishwowater/projects/3DSceneAgent/scene_agent/session/owner_proxy.py`。  
实现 JSON 与 SSE 代理，附带 `X-Session-Proxy-Hop`（最大 1）防循环。
4. 新增 `/Users/fishwowater/projects/3DSceneAgent/scene_agent/agent/redis_checkpointer.py`。  
继承 `BaseCheckpointSaver[str]`，实现 `get_tuple/list/put/put_writes/delete_thread` 及 async 对应方法，序列化与 `InMemorySaver` 行为一致。
5. 新增 `/Users/fishwowater/projects/3DSceneAgent/scene_agent/memory/reference_image_store.py`。  
把参考图元数据从进程内迁移到 Redis，文件继续写共享 POSIX 路径。
6. 重构 `/Users/fishwowater/projects/3DSceneAgent/scene_agent/memory/reference_image_memory.py`。  
保留现有 API（`add_images/list_images/clear_*`），底层改为 Redis store，避免上层调用点大改。
7. 重构 `/Users/fishwowater/projects/3DSceneAgent/scene_agent/agent/graph.py`。  
把 `MemorySaver` 改为注入式 checkpointer（Redis），`create_agent_graph()` 使用全局 Redis checkpointer。
8. 重构 `/Users/fishwowater/projects/3DSceneAgent/scene_agent/blender/session_manager.py`。  
保留“本地 runtime 句柄管理”职责，移除“全局真相”职责；端口分配改为经 Redis 预占。
9. 重构 `/Users/fishwowater/projects/3DSceneAgent/scene_agent/interfaces/api.py`。  
为所有 `thread_id` 相关 HTTP/SSE 入口统一接入 `claim_or_proxy`；owner 本地执行，非 owner 转发；删除 `/ws`；`/threads` 读 Redis；`/health` 增加 Redis 诊断；sweeper 改为“仅处理本 worker 所有权会话且操作前复核 lease token”。
10. 重构 `/Users/fishwowater/projects/3DSceneAgent/scene_agent/interfaces/api.py` 的 VLM 选择存储。  
`_thread_vlm_configs` 从进程内 dict 改为 Redis hash（线程级 provider/model）。
11. 更新 `/Users/fishwowater/projects/3DSceneAgent/.env.example`、`/Users/fishwowater/projects/3DSceneAgent/scripts/run_headless.sh`、`/Users/fishwowater/projects/3DSceneAgent/README.md`。  
增加 Redis 与多进程多端口部署示例，明确每进程 `API_WORKER_ADVERTISE_URL` 唯一。

## 请求流与故障流（实施标准）
1. 正常请求：先 `claim_or_get_owner`，owner 本地执行，非 owner 代理执行。
2. 非 owner 代理失败：若 owner 不可达且 lease 过期，执行 `force-claim` 接管并本地重启 runtime，从共享 `blend_path` 恢复。
3. SSE 代理：字节流透传，不重组事件；保留 `text/event-stream` 与 keepalive。
4. Idle 回收：各 worker 仅扫描自己 owner 的 session，回收前后二次校验 lease token + `last_active_ms`。

## 测试计划
1. 单测新增：`/Users/fishwowater/projects/3DSceneAgent/tests/unit/test_redis_registry.py`。  
覆盖 claim/renew/force-claim 原子性、fencing、端口预占冲突。
2. 单测新增：`/Users/fishwowater/projects/3DSceneAgent/tests/unit/test_redis_checkpointer.py`。  
覆盖 `put/get/list/delete_thread` 与 async 版本、`before/filter/limit`、pending writes。
3. 单测新增：`/Users/fishwowater/projects/3DSceneAgent/tests/unit/test_reference_image_store_redis.py`。  
覆盖顺序、上限、元数据恢复、文件缺失容错。
4. 集成测试新增：`/Users/fishwowater/projects/3DSceneAgent/tests/integration/test_multiworker_owner_proxy.py`。  
两个 API 进程竞争同一 `thread_id`，仅一个 owner 启动 runtime，另一个正确代理。
5. 集成测试新增：`/Users/fishwowater/projects/3DSceneAgent/tests/integration/test_multiworker_sse_proxy.py`。  
验证 `/chat/stream` 经非 owner 进入时的 SSE 透传正确性。
6. 集成测试新增：`/Users/fishwowater/projects/3DSceneAgent/tests/integration/test_owner_failover_takeover.py`。  
模拟 owner crash + lease 过期，验证接管与 `.blend` 恢复。
7. 现有测试改造：所有依赖 `_thread_vlm_configs`、`MemorySaver`、`ReferenceImageMemory._images` 的测试改为 Redis 后端断言。
8. 回归命令基线：`pytest tests/unit`、`pytest tests/integration`（含多进程场景）。

## 发布与回滚
1. 上线前准备 Redis（AOF 开启）与共享存储路径。
2. 以“多进程多端口，每进程 1 worker”启动 API，每进程设置唯一 `API_WORKER_ID` 与 `API_WORKER_ADVERTISE_URL`。
3. 完成 smoke：`/chat/stream`、`/scene/*`、`/todos/{thread_id}`、`/threads`、owner 接管场景。
4. 回滚时直接回退服务版本；共享 `.blend` 文件可复用；Redis key 可保留以便故障分析。

## Assumptions 与默认值
1. 假设所有 API 进程互相可达其 `API_WORKER_ADVERTISE_URL`。
2. 假设共享存储路径在所有进程中一致且具备读写权限。
3. 默认 `REDIS_KEY_PREFIX=sa`、`SESSION_LEASE_TTL_SECONDS=20`、`SESSION_HEARTBEAT_INTERVAL_SECONDS=5`、`SESSION_OWNER_UNREACHABLE_GRACE_SECONDS=10`。
4. 默认 `SESSION_SHARED_STORAGE_ROOT=/tmp/scene_agent_sessions`（生产需改为真实共享盘）。
5. 本次不支持 `uvicorn --workers N` 单端口多 worker 作为生产拓扑；采用多进程多端口方式实现 multiple API workers。
