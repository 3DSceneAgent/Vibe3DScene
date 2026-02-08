# Blender 连接架构重构 - 测试总结

## 修复的问题

### 1. ✅ 死锁问题（最严重）
- **症状**：api.py 卡在 `with session.lock:` 无法继续
- **原因**：嵌套锁 - `send_blender_command_sync()` 持有锁后调用 `get_blender_connection_for_thread()` 再次尝试获取同一个锁
- **修复**：移除外层锁，避免嵌套

### 2. ✅ Headless 进程立即退出
- **原因**：`bpy.app.timers` 在 --background 模式下不工作
- **修复**：重构为纯同步 blocking 架构，命令直接在主线程执行

### 3. ✅ Headless 无法启动
- **原因**：`BLENDER_HEADLESS_ARGS` 环境变量被 shell 截断
- **修复**：使用 `: "${VAR:=value}"` + `export VAR` 语法

### 4. ✅ 单连接阻塞
- **原因**：server accept() 后同步处理，阻塞后续连接
- **修复**：实现多客户端支持（accept 线程 + 客户端线程 + 命令队列）

### 5. ✅ GUI 模式被移除
- **修复**：恢复 operators, ui, 支持 blocking=False 模式

## 测试结果

### Test 1: Headless Auto-Managed 模式
```
curl http://localhost:8000/scene/test-thread-1
✅ SUCCESS: Blender 自动启动 (PID: 41793)
✅ 场景数据正确返回 (3 objects)
```

### Test 2: 多客户端并发
```
10 个客户端同时连接
✅ SUCCESS: 全部完成
✅ 加速比: 2.53x (vs 顺序执行)
```

### Test 3: Web + Agent 并发
```
Web: 渲染（耗时操作）
Agent: 同时查询场景信息（3次）
✅ SUCCESS: Agent 在 Web 渲染期间完成查询
✅ 总耗时: 1.46s
```

### Test 4: Local-client 模式
```
手动启动 Blender (port 9876)
API 使用 BLENDER_MODE=local-client
✅ SUCCESS: 正确连接并返回数据
```

### Test 5: Render 功能
```
curl http://localhost:8000/scene/{thread_id}/renders
✅ SUCCESS: 1 camera rendered
```

## 架构改进

### 旧架构（失败）
```
主线程: while sleep(1)
daemon 线程: accept() → recv()
bpy.app.timers.register() ❌ 在 headless 不工作
```

### 新架构（成功）
```
Accept 线程: accept() → 创建客户端线程
客户端线程: recv() → 放入命令队列
主线程: 从队列取命令 → 执行 → 返回结果
```

## 支持的模式

1. **Headless Auto-Managed**: API 自动为每个 thread_id 启动 Blender
2. **Local-client**: 连接到手动启动的 Blender（GUI 或 headless）
3. **GUI**: Blender UI 中启动服务器，使用 timer 处理命令

## 性能特性

- ✅ 多客户端并发（无阻塞）
- ✅ Web 和 Agent 可同时使用
- ✅ 命令队列保证 Blender API 线程安全
- ✅ Headless 进程保持运行

