---
name: Headless Blender 纯同步重构
overview: 将 Blender MCP addon 从线程+timer 架构重构为纯同步主线程架构，解决 headless 模式下 bpy.app.timers 失效导致的请求无响应问题。同时移除所有 GUI 相关组件，简化为仅支持 headless 模式。
todos:
  - id: refactor-server
    content: 重构 server.py 为纯同步架构：移除线程、移除 bpy.app.timers、实现 blocking server loop
    status: completed
  - id: update-headless-client
    content: 更新 blender_headless_client.py：移除 keepalive loop、调用 blocking 启动函数
    status: completed
  - id: add-blocking-entrypoint
    content: 在 __init__.py 添加 start_server_blocking() 函数供 headless 调用
    status: completed
  - id: remove-gui-components
    content: 移除 GUI 相关组件：ui.py 面板、start/stop operators、相关 properties
    status: completed
  - id: test-headless-mode
    content: 测试 headless 模式：启动服务、发送请求、验证响应正常
    status: completed
isProject: false
---

# Headless Blender 纯同步重构方案

## 问题根因

当前 `[addon/blender_mcpv_addon/server.py](addon/blender_mcpv_addon/server.py)` 的架构存在致命缺陷：

```python
# Line 48-50: daemon 线程运行 server loop
self.server_thread = threading.Thread(target=self._server_loop)
self.server_thread.daemon = True
self.server_thread.start()

# Line 148: 使用 timer 调度到主线程执行
bpy.app.timers.register(execute_wrapper, first_interval=0.0)
```

**问题**：`bpy.app.timers` 依赖 Blender UI 事件循环，在 `--background` 模式下不工作，导致：

1. 命令接收成功，但 timer 队列不被处理
2. 命令永远不执行，客户端超时
3. 即使 `[scripts/blender_headless_client.py:127-132](scripts/blender_headless_client.py)` 有 keepalive loop，timer 也无法运行

参考 [StackExchange 成熟方案](https://blender.stackexchange.com/questions/271096/)，纯同步架构在 headless 模式下完全可行。

## 架构变化

### 当前架构（失败）

```
主线程: while sleep(1)  [keepalive but idle]
  ↓
daemon 线程: accept() → recv()
  ↓
bpy.app.timers.register()  [调度到主线程]
  ↓
主线程事件循环 [不存在于 --background 模式]
  ↓
命令永远不执行 ❌
```

### 新架构（可行）

```
主线程: 
  while True:
    accept() → recv() → execute_command() → send()
    ↓
    直接同步执行，无线程切换 ✅
```

## 核心修改

### 1. 重构 `[addon/blender_mcpv_addon/server.py](addon/blender_mcpv_addon/server.py)`

**关键改动**：

- 移除所有线程相关代码（`server_thread`, `client_thread`, `daemon=True`）
- 将 `start()` 方法改为 blocking，在主线程运行 server loop
- 移除 `bpy.app.timers.register()` 调用（Line 148）
- 命令直接在主线程同步执行

**新的 `start()` 方法签名**：

```python
def start(self, blocking=True):
    """
    Start socket server.
    
    Args:
        blocking: If True (headless), run server loop in main thread.
                  Must be True for headless mode.
    """
```

**新的 server loop**：

```python
def _server_loop_blocking(self):
    """Blocking server loop for headless mode - runs in main thread"""
    self.socket.settimeout(1.0)
    
    while self.running:
        try:
            client, address = self.socket.accept()
            print(f"Connected: {address}")
            
            # Handle client synchronously in main thread
            self._handle_client_blocking(client)
        except socket.timeout:
            continue
        except Exception as e:
            if not self.running:
                break
```

**新的 client handler**：

```python
def _handle_client_blocking(self, client):
    """Handle client connection synchronously in main thread"""
    client.settimeout(None)
    buffer = b''
    
    try:
        while self.running:
            data = client.recv(8192)
            if not data:
                break
            
            buffer += data
            try:
                command = json.loads(buffer.decode('utf-8'))
                buffer = b''
                
                # Execute directly in main thread (no timer!)
                response = self.execute_command(command)
                client.sendall(json.dumps(response).encode('utf-8'))
            except json.JSONDecodeError:
                pass  # Wait for more data
    finally:
        client.close()
```

### 2. 简化 `[scripts/blender_headless_client.py](scripts/blender_headless_client.py)`

**移除不必要的 keepalive loop**（Line 127-132）：

```python
# 旧代码（不需要了）:
# try:
#     while True:
#         time.sleep(1)
# except KeyboardInterrupt:
#     return 0

# 新代码: server.start() 本身就是 blocking 的
```

**新的 `main()` 函数逻辑**：

```python
def main() -> int:
    # ... 现有的 addon 启动逻辑 ...
    
    # Start server in blocking mode (runs in main thread)
    if not started:
        print("Addon enabled but server not started")
        return 1
    
    # Server loop is now blocking - no need for keepalive
    # Process will stay alive until server is stopped
    return 0
```

**调用入口点**：需要确保 addon 的启动函数支持 blocking 模式：

```python
def _call_addon_function(module: str, host: str, port: int) -> bool:
    addon = importlib.import_module(module)
    # 查找启动函数，传递 blocking=True
    for func_name in ("start_server_blocking", "start_server", ...):
        func = getattr(addon, func_name, None)
        if callable(func):
            try:
                func(host=host, port=port, blocking=True)
            except TypeError:
                func(host=host, port=port)
            return True
    return False
```

### 3. 提供 headless 启动入口 `[addon/blender_mcpv_addon/__init__.py](addon/blender_mcpv_addon/__init__.py)`

添加模块级函数供 headless client 调用：

```python
def start_server_blocking(host='localhost', port=9876):
    """
    Start server in blocking mode for headless Blender.
    This function will not return until server is stopped.
    """
    from .server import BlenderMCPVisionServer
    
    if not hasattr(bpy.types, "blendermcpv_server"):
        bpy.types.blendermcpv_server = BlenderMCPVisionServer(host, port)
    
    server = bpy.types.blendermcpv_server
    server.start(blocking=True)  # Blocking call
```

### 4. 移除 GUI 组件（可选，简化架构）

由于不再需要 GUI 模式，可以移除：

- `[addon/blender_mcpv_addon/operators.py](addon/blender_mcpv_addon/operators.py)`：BLENDERMCPV_OT_StartServer, BLENDERMCPV_OT_StopServer
- `[addon/blender_mcpv_addon/ui.py](addon/blender_mcpv_addon/ui.py)`：整个 UI 面板
- `[addon/blender_mcpv_addon/__init__.py](addon/blender_mcpv_addon/__init__.py)`：移除 UI 相关的 scene properties 和注册

**保留**：

- chat 相关 operators（如果 headless 也需要）
- 核心 scene properties（如 port, backend_url）

### 5. 更新 `[scripts/start_services.py](scripts/start_services.py)`

确保 headless 参数正确传递：

```python
# Line 44: 已经正确，无需修改
"--background --python scripts/blender_headless_client.py -- --host {host} --port {port}",
```

## 测试验证

### 验证步骤

1. 启动 headless Blender：
  ```bash
   ./scripts/start_services.sh
  ```
2. 检查 Blender 进程是否持续运行（不立即退出）
3. 从前端或 API 发送请求：
  - `GET /scene/{thread_id}`
  - `GET /scene/{thread_id}/renders`
  - `GET /scene/{thread_id}/gltf`
4. 确认响应正常返回（不超时）

### 预期结果

- ✅ Blender 进程在 `server.start(blocking=True)` 处阻塞，保持运行
- ✅ Socket server 在主线程同步处理请求
- ✅ 命令直接执行，无需 timer 调度
- ✅ 响应立即返回给客户端
- ✅ 日志显示命令执行过程

## 架构优势

1. **简单可靠**：移除多线程复杂性，避免竞态条件
2. **Headless 兼容**：不依赖任何 UI 事件循环
3. **性能足够**：对于一次一个请求的场景，同步处理延迟可接受
4. **成熟模式**：参考 StackExchange 验证过的方案
5. **易维护**：代码流程线性，易于调试

## 潜在问题

**问：单连接限制？**
答：当前设计本就是每个 session_id 一个 Blender 进程，单连接足够。

**问：执行耗时操作会阻塞？**
答：是的，但这符合设计——每个请求需要完整执行后再处理下一个。如需并发，应该启动多个 Blender 进程（通过 session_id）。

**问：如何优雅停止？**
答：可以通过发送特殊命令（如 `{"type": "shutdown"}`）或进程信号（SIGTERM）触发 `self.running = False`。