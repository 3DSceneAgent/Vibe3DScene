## Summary: How This Differs from the `blender-mcp + cc` Approach

Compared with the `blender-mcp + cc` setup, the core difference is that, on top of recreating the fundamentals of a coding-agent harness, this system introduces harness components and MCP tools specifically designed for 3D modeling.

1. Explicit management of scene-related short-term memory.
2. Decoupling Blender headless execution to support cross-platform operation. The system can easily scale up for production.
3. Custom agent orchestration, such as render-and-verify loops, dual agents, and agent-based mesh penetration/intersection detection.
4. Stronger multimodal capabilities, including improved rendering and camera management.
5. Basic editing and reference capabilities introduced on the web side.
