# MCP Server Split Assessment

## Context

Current `mcp_server/server.py` has grown to include:
- Core Blender tools (scene/object/screenshot/camera/render, PolyHaven)
- Asset tools (retrieval, procedural generation, Rodin bridge, Hunyuan server-side polling)
- Registration and runtime-gating logic

This causes high coupling and increases maintenance cost.

## Recommended Split (Code Module Level, Single Runtime)

Use capability-oriented module split while keeping one `FastMCP` process and one port:

- `mcp_server/tools/base.py`
  - Scene/object/screenshot/code execution
  - GLB import primitives used by other tool domains
- `mcp_server/tools/multimodal/camera_tools.py`
  - Camera control and rendering tools
- `mcp_server/tools/asset_retrieval/polyhaven.py`
  - PolyHaven search/download/material assignment
- `mcp_server/tools/asset_retrieval/sketchfab.py`
  - Sketchfab retrieval + server-side import flow
- `mcp_server/tools/asset_retrieval/objaverse_retrieval.py`
  - Retrieval database search/import
- `mcp_server/tools/asset_gen/trellis2.py`
  - TRELLIS2 (headless)
- `mcp_server/tools/asset_gen/rodin.py`
  - Rodin bridge tools (local-client)
- `mcp_server/tools/asset_gen/hunyuan3d.py`
  - Hunyuan official API tool (headless, server-side polling)
- `mcp_server/tools/pcg/infinigen.py`
  - Infinigen/PCG integrations
- `mcp_server/tools/memory/session_tools.py`
  - Session persistence status and rollback
- `mcp_server/tools/strategy.py`
  - Asset-creation strategy prompt text
- `mcp_server/tool_registry.py`
  - `register_mcp_tools()` and conditional registration
  - Mode + env gates (`BLENDER_MODE`, `ENABLE_RODIN`, `ENABLE_HUNYUAN`, `ENABLE_TRELLIS2`)
  - Service-health dependency checks

## Why Not Split into Multiple MCP Servers Now

Running multiple MCP server processes (multi-port) adds:
- Process management complexity in headless sessions
- More health checks and startup synchronization points
- Higher operational overhead and more failure modes

Given current scale, these costs are higher than the benefit.

## Decision

Adopt **single-process, single-port** runtime; only split code into modules.

## Migration Plan

1. Extract pure helper functions and tool functions into `tools/` modules.
2. Move existing gating and dependency checks into `tool_registry.py`.
3. Keep `mcp_server/server.py` as thin entrypoint:
   - create `FastMCP`
   - initialize connection
   - call registry
   - run transport
4. Add regression checks:
   - Headless with `ENABLE_TRELLIS2=true`: TRELLIS2 tool exposed
   - Headless with `ENABLE_HUNYUAN=true`: only expected Hunyuan tools exposed
   - Local-client with `ENABLE_RODIN=true`: Rodin bridge tools exposed
   - Disabled flags: corresponding tools hidden

## Trigger for Future Multi-Server Split

Only consider real multi-server split when one of these appears:
- Need independent autoscaling for asset tools vs core Blender tools
- Resource isolation requirement (CPU/memory/network) between tool domains
- Distinct release cadence requiring separate deployment units
