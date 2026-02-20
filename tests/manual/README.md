# Manual Tests for Blender Integration

This directory contains manual integration tests for the Blender MCP server and headless mode.

## Test Scripts

### 1. `test_direct_socket.py`
Direct socket connection test - verifies basic Blender server communication.

```bash
# Test with default settings (localhost:9876)
python tests/manual/test_direct_socket.py

# Test with custom port
python tests/manual/test_direct_socket.py --port 9877

# Test specific object
python tests/manual/test_direct_socket.py --object Camera
```

**What it tests:**
- Socket connection to Blender server
- `get_scene_info` command
- `execute_code` command
- `get_object_info` command

### 2. `test_multi_client_concurrent.py`
Multi-client concurrent access test - verifies that multiple clients can connect simultaneously.

```bash
# Test with default settings
python tests/manual/test_multi_client_concurrent.py

# Test with custom configuration
python tests/manual/test_multi_client_concurrent.py --port 9876 --sequential 3 --concurrent 10
```

**What it tests:**
- Sequential baseline (3 clients)
- Concurrent execution (5-10 clients)
- Speedup calculation
- Connection handling

**Expected result:** Concurrent execution should be significantly faster (>1.5x speedup).

### 3. `test_web_agent_concurrent.py`
Web + Agent concurrent access test - simulates real-world usage.

```bash
# Test with default settings
python tests/manual/test_web_agent_concurrent.py

# Test with custom configuration
python tests/manual/test_web_agent_concurrent.py --port 9876 --agent-requests 5
```

**What it tests:**
- Web application doing long render operation
- Agent simultaneously querying scene info (3 times)
- No blocking between different clients

**Expected result:** Agent should complete queries while Web is rendering.

### 4. `test_headless_automanaged.py`
Headless auto-managed mode test - verifies automatic Blender instance management.

```bash
# Prerequisites: Set BLENDER_MODE=headless and start services
export BLENDER_MODE=headless
./scripts/start_services.sh

# Then in another terminal:
python tests/manual/test_headless_automanaged.py
```

**What it tests:**
- API automatically starts Blender for new thread_id
- Multiple threads can use the system
- Render functionality works
- Process IDs are recorded

### 5. `validate_prompt.py`
Manual end-to-end prompt validator (API mode or pure invoke mode).

```bash
# API streaming mode (requires API server running)
python tests/manual/validate_prompt.py \
  --mode api \
  --base-url http://127.0.0.1:8000 \
  --proxy http://127.0.0.1:7890 \
  --prompt "Create a low poly scene in a dungeon, with a dragon guarding a pot of gold"

# Pure Python invoke mode (no /chat API required)
python tests/manual/validate_prompt.py \
  --mode invoke \
  --proxy http://127.0.0.1:7890 \
  --prompt "Create a blue cube"
```

**What it reports:**
- Graph step progression and tail nodes
- Latest verification payload (if present)
- Todo states (`latest_todos_event` + persisted `todos_state`)
- Final assistant summary tail
- Error event / done event metadata

## Running All Tests

```bash
# Start Blender manually (for tests 1-3)
blender --background --python scripts/blender_headless_client.py -- --host localhost --port 9876 &

# Run tests
python tests/manual/test_direct_socket.py
python tests/manual/test_multi_client_concurrent.py
python tests/manual/test_web_agent_concurrent.py

# For test 4, restart with headless mode
pkill blender
export BLENDER_MODE=headless
./scripts/start_services.sh
python tests/manual/test_headless_automanaged.py
```

## Interpreting Results

### Success Indicators
- ✅ All tests print "SUCCESS" or "PASS"
- ✅ Multi-client shows speedup > 1.5x
- ✅ Web + Agent test completes in < 3s
- ✅ Headless shows process_id in logs

### Failure Indicators
- ❌ Connection timeout or refused
- ❌ Commands return error status
- ❌ No speedup in concurrent test (indicates blocking)
- ❌ process_id: null in headless mode

## Troubleshooting

### "Connection refused"
- Ensure Blender server is running on the correct port
- Check `ps aux | grep blender` for processes
- Check server logs in `/tmp/scene_agent_headless_logs/`

### "Command timeout"
- Check if Blender process is alive
- Verify server is in blocking mode (check logs)
- Ensure `bpy.app.timers` is not being used in headless

### "No speedup in concurrent test"
- Multi-client support may not be enabled
- Check server logs for "multi-client mode"
- Verify accept thread is running

### "process_id: null" in headless mode
- Check `BLENDER_HEADLESS_CMD` and `BLENDER_HEADLESS_ARGS` env vars
- Run: `echo $BLENDER_HEADLESS_ARGS` (should be full command)
- Check session manager logs for startup errors
