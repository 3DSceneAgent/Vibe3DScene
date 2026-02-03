# Service Management Scripts

This directory contains scripts for managing the 3D Scene Agent services.

## Starting Services

You can use either the bash or Python script to start both the MCP server and API server simultaneously:

### Option 1: Bash Script (Linux/macOS)

```bash
./scripts/start_services.sh
```

### Option 2: Python Script (Cross-platform)

```bash
python scripts/start_services.py
```

Both scripts will:
1. Start the MCP server (`python -m mcp.server`)
2. Start the API server (`python main.py --mode api`)
3. Monitor both processes
4. Handle graceful shutdown when you press `Ctrl+C`

Both scripts also set default headless Blender environment variables if not provided:
- `BLENDER_HEADLESS_CMD=blender`
- `BLENDER_HEADLESS_ARGS=--background --python scripts/blender_headless_client.py -- --host {host} --port {port}`

## Stopping Services

Simply press `Ctrl+C` in the terminal where the script is running. Both services will be stopped automatically.

## What's Running

- **MCP Server**: Blender integration server (running in background)
- **API Server**: REST API on http://0.0.0.0:8000

## CLI Mode Improvements

The CLI interface (`python main.py --mode cli`) now supports:

- **Multiple exit commands**: `exit`, `quit`, `q`, `/exit`, `/quit`
- **Ctrl+C**: Cleanly exits the application
- **No echo**: User input is no longer echoed back
- **Parsed responses**: JSON responses are automatically parsed to show clean text
- **Better streaming**: Responses are displayed as they arrive

## Example Usage

```bash
# Start services in background
python scripts/start_services.py &

# Use CLI in another terminal
cd /path/to/3DSceneAgent
python main.py --mode cli

# Or use the API
curl http://localhost:8000/
```

## Troubleshooting

If services fail to start:

1. Check that you have a `.env` file with `VLM_API_KEY` set
2. Ensure no other processes are using the same ports
3. Check the MCP server log at `/tmp/mcp_server.log` (bash script only)
4. Verify all dependencies are installed: `pip install -r requirements.txt`
