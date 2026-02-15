"""Multimodal camera and rendering MCP tools."""

from mcp_server.tools.multimodal.camera_tools import (
    camera_act,
    camera_observe,
    camera_set_pose,
    render_from_camera,
    render_from_objects,
)

__all__ = [
    "camera_act",
    "camera_observe",
    "camera_set_pose",
    "render_from_camera",
    "render_from_objects",
]
