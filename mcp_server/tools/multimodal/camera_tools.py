from __future__ import annotations

import logging
import math
import os
import tempfile
import time
from typing import Any, Callable, Optional
from urllib.parse import unquote, urlparse

from mcp.server.fastmcp import Context
from mcp.types import CallToolResult
from PIL import Image as PILImage

from mcp_server import runtime
from scene_agent.utils.rendering import RENDERS_DIR, process_and_save_render

logger = logging.getLogger("BlenderMCPServer")
BlenderCommandSender = Callable[[str, dict[str, Any] | None], dict[str, Any]]

# ---------------------------------------------------------------------------
# Scene-level camera constants
# ---------------------------------------------------------------------------
_SCENE_CAMERA_VIEW_CONFIGS: tuple[tuple[str, float, float], ...] = (
    ("SceneCamera_NE", 45.0, 30.0),
    ("SceneCamera_SW", -135.0, 30.0),
    ("SceneCamera_TopDown", 0.0, 89.0),
)
SCENE_CAMERA_NAMES = tuple(config[0] for config in _SCENE_CAMERA_VIEW_CONFIGS)
_SCENE_CAMERA_FOCAL_MM = 42.0
_SCENE_CAMERA_SENSOR_WIDTH = 36.0
_SCENE_CAMERA_DISTANCE_MARGIN = 1.3
_SCENE_CAMERA_MIN_DISTANCE = 1.25
_SCENE_CAMERA_MAX_DISTANCE = 650.0
_SCENE_BBOX_OUTLIER_DIM_RATIO = 12.0
_SCENE_BBOX_OUTLIER_MIN_DIM = 4.0
_SCENE_BBOX_TRIM_REQUIRED_SHRINK = 0.6
_SCENE_GRID_CAMERA_NAME = "SceneGlobalGrid"


def _coerce_xyz(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    try:
        return [float(value[0]), float(value[1]), float(value[2])]
    except (TypeError, ValueError):
        return None


def _center_dimensions_to_min_max(
    center: Any,
    dimensions: Any,
) -> tuple[list[float], list[float]] | None:
    center_xyz = _coerce_xyz(center)
    dims_xyz = _coerce_xyz(dimensions)
    if center_xyz is None or dims_xyz is None:
        return None
    half = [max(value, 0.0) / 2.0 for value in dims_xyz]
    bbox_min = [center_xyz[i] - half[i] for i in range(3)]
    bbox_max = [center_xyz[i] + half[i] for i in range(3)]
    return bbox_min, bbox_max


def _extract_bbox_min_max(raw_bbox: Any) -> tuple[list[float], list[float]] | None:
    if isinstance(raw_bbox, (list, tuple)) and len(raw_bbox) >= 2:
        bbox_min = _coerce_xyz(raw_bbox[0])
        bbox_max = _coerce_xyz(raw_bbox[1])
        if bbox_min is not None and bbox_max is not None:
            return bbox_min, bbox_max
        return None

    if not isinstance(raw_bbox, dict):
        return None

    for min_key, max_key in (
        ("min", "max"),
        ("bbox_min", "bbox_max"),
        ("min_corner", "max_corner"),
    ):
        bbox_min = _coerce_xyz(raw_bbox.get(min_key))
        bbox_max = _coerce_xyz(raw_bbox.get(max_key))
        if bbox_min is not None and bbox_max is not None:
            return bbox_min, bbox_max

    return _center_dimensions_to_min_max(
        raw_bbox.get("center"),
        raw_bbox.get("dimensions"),
    )


def _extract_object_bbox_min_max(obj: dict[str, Any]) -> tuple[list[float], list[float]] | None:
    # Prefer explicit world-space bbox keys before generic ones.
    for key in ("world_bounding_box", "bbox", "bounding_box"):
        if key not in obj:
            continue
        parsed = _extract_bbox_min_max(obj.get(key))
        if parsed is not None:
            return parsed
    return None


def _bbox_record_from_bounds(
    obj: dict[str, Any],
    bounds: tuple[list[float], list[float]],
) -> dict[str, Any] | None:
    bbox_min_raw, bbox_max_raw = bounds
    if len(bbox_min_raw) != 3 or len(bbox_max_raw) != 3:
        return None

    bbox_min = [float(min(bbox_min_raw[i], bbox_max_raw[i])) for i in range(3)]
    bbox_max = [float(max(bbox_min_raw[i], bbox_max_raw[i])) for i in range(3)]
    if not all(math.isfinite(value) for value in (*bbox_min, *bbox_max)):
        return None

    dimensions = [max(bbox_max[i] - bbox_min[i], 0.0) for i in range(3)]
    max_dim = max(dimensions)
    if max_dim <= 1e-6:
        return None

    center = [(bbox_min[i] + bbox_max[i]) / 2.0 for i in range(3)]
    raw_name = obj.get("name")
    object_name = raw_name.strip() if isinstance(raw_name, str) else ""
    return {
        "name": object_name,
        "min": bbox_min,
        "max": bbox_max,
        "center": center,
        "dimensions": dimensions,
        "max_dim": max_dim,
    }


def _union_bbox_from_records(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not records:
        return None
    union_min = [min(record["min"][i] for record in records) for i in range(3)]
    union_max = [max(record["max"][i] for record in records) for i in range(3)]
    center = [(union_min[i] + union_max[i]) / 2.0 for i in range(3)]
    dimensions = [union_max[i] - union_min[i] for i in range(3)]
    return {"center": center, "dimensions": dimensions, "min": union_min, "max": union_max}


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return sorted_values[0]

    q = min(max(float(quantile), 0.0), 1.0)
    position = (len(sorted_values) - 1) * q
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return sorted_values[low]
    weight = position - low
    return sorted_values[low] * (1.0 - weight) + sorted_values[high] * weight


def _select_focus_bbox_records(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    if len(records) < 2:
        return records, []

    max_dims = [float(record["max_dim"]) for record in records]
    baseline_dim = max(_percentile(max_dims, 0.25), 1e-6)
    outlier_threshold = max(
        baseline_dim * _SCENE_BBOX_OUTLIER_DIM_RATIO,
        _SCENE_BBOX_OUTLIER_MIN_DIM,
    )

    inliers = [record for record in records if float(record["max_dim"]) <= outlier_threshold]
    if not inliers or len(inliers) == len(records):
        return records, []

    full_union = _union_bbox_from_records(records)
    inlier_union = _union_bbox_from_records(inliers)
    if full_union is None or inlier_union is None:
        return records, []

    full_max_span = max(full_union["dimensions"])
    inlier_max_span = max(inlier_union["dimensions"])
    if full_max_span <= 1e-6:
        return records, []

    # Apply trimming only when it materially improves framing stability.
    if inlier_max_span > full_max_span * _SCENE_BBOX_TRIM_REQUIRED_SHRINK:
        return records, []

    excluded_names: list[str] = []
    inlier_names = {record.get("name") for record in inliers}
    for record in records:
        object_name = record.get("name")
        if isinstance(object_name, str) and object_name and object_name not in inlier_names:
            excluded_names.append(object_name)

    return inliers, excluded_names


def _compute_union_aabb(scene_info: dict[str, Any]) -> dict[str, Any] | None:
    """Compute union AABB from scene info object bounds.

    Supports both:
    - world_bounding_box: [[min_x, min_y, min_z], [max_x, max_y, max_z]]
    - bbox: {"center": [...], "dimensions": [...]}
    """
    objects = scene_info.get("objects") or scene_info.get("scene_objects") or {}
    if isinstance(objects, list):
        obj_list = objects
    elif isinstance(objects, dict):
        obj_list = list(objects.values())
    else:
        return None

    bbox_records: list[dict[str, Any]] = []
    for obj in obj_list:
        if not isinstance(obj, dict):
            continue
        parsed = _extract_object_bbox_min_max(obj)
        if parsed is None:
            continue
        record = _bbox_record_from_bounds(obj, parsed)
        if record is not None:
            bbox_records.append(record)

    if bbox_records:
        focus_records, excluded_names = _select_focus_bbox_records(bbox_records)
        union_bbox = _union_bbox_from_records(focus_records)
        if union_bbox is None:
            union_bbox = _union_bbox_from_records(bbox_records)
        if union_bbox is not None:
            object_names = [
                record["name"]
                for record in focus_records
                if isinstance(record.get("name"), str) and record["name"]
            ]
            union_bbox["object_names"] = object_names
            if excluded_names:
                union_bbox["excluded_object_names"] = excluded_names
            return union_bbox

    # Fallback to scene-level bbox if object-level bboxes are unavailable.
    scene_bbox = _extract_bbox_min_max(scene_info.get("scene_bbox"))
    if scene_bbox is None:
        return None

    fallback_record = _bbox_record_from_bounds({}, scene_bbox)
    if fallback_record is None:
        return None
    union_bbox = _union_bbox_from_records([fallback_record])
    if union_bbox is not None:
        union_bbox["object_names"] = []
    return union_bbox


def _fov_aware_distance(dimensions: list[float]) -> float:
    """Calculate camera distance so the bbox fits comfortably in frame."""
    max_dim = max(max(dimensions), 0.1)
    fov_h = 2 * math.atan(_SCENE_CAMERA_SENSOR_WIDTH / (2 * _SCENE_CAMERA_FOCAL_MM))
    raw_distance = (max_dim / math.tan(fov_h / 2)) * _SCENE_CAMERA_DISTANCE_MARGIN
    if not math.isfinite(raw_distance):
        return _SCENE_CAMERA_MIN_DISTANCE
    return min(max(raw_distance, _SCENE_CAMERA_MIN_DISTANCE), _SCENE_CAMERA_MAX_DISTANCE)


def _spherical_to_cartesian(
    center: list[float], distance: float, azimuth_deg: float, elevation_deg: float
) -> list[float]:
    """Convert spherical coords around *center* to a world-space position."""
    az = math.radians(azimuth_deg)
    el = math.radians(elevation_deg)
    x = center[0] + distance * math.cos(el) * math.sin(az)
    y = center[1] + distance * math.cos(el) * math.cos(az)
    z = center[2] + distance * math.sin(el)
    return [x, y, z]


def _vector_cross(a: list[float], b: list[float]) -> list[float]:
    return [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]


def _vector_normalize(vec: list[float]) -> list[float] | None:
    length = math.sqrt(sum(component * component for component in vec))
    if length <= 1e-9:
        return None
    return [component / length for component in vec]


def _look_at_rotation_euler(position: list[float], target: list[float]) -> list[float]:
    """
    Build Blender-compatible XYZ Euler rotation so camera local -Z tracks target.

    Equivalent to Blender's ``to_track_quat("-Z", "Y")`` behavior for a world-up
    preference, implemented here without Blender runtime dependencies.
    """
    forward = _vector_normalize([target[i] - position[i] for i in range(3)])
    if forward is None:
        return [0.0, 0.0, 0.0]

    world_up = [0.0, 0.0, 1.0]
    dot = sum(forward[i] * world_up[i] for i in range(3))
    if abs(dot) > 0.999:
        world_up = [0.0, 1.0, 0.0]

    z_axis = [-forward[0], -forward[1], -forward[2]]
    x_axis = _vector_normalize(_vector_cross(world_up, z_axis))
    if x_axis is None:
        world_up = [1.0, 0.0, 0.0]
        x_axis = _vector_normalize(_vector_cross(world_up, z_axis))
    if x_axis is None:
        return [0.0, 0.0, 0.0]
    y_axis = _vector_cross(z_axis, x_axis)

    m00, _, _ = x_axis[0], y_axis[0], z_axis[0]
    m10, m11, m12 = x_axis[1], y_axis[1], z_axis[1]
    m20, m21, m22 = x_axis[2], y_axis[2], z_axis[2]

    m20_clamped = min(max(m20, -1.0), 1.0)
    if m20_clamped < 1.0:
        if m20_clamped > -1.0:
            y = math.asin(-m20_clamped)
            x = math.atan2(m21, m22)
            z = math.atan2(m10, m00)
        else:
            y = math.pi / 2.0
            x = -math.atan2(-m12, m11)
            z = 0.0
    else:
        y = -math.pi / 2.0
        x = math.atan2(-m12, m11)
        z = 0.0
    return [x, y, z]


def _render_scene_camera_with_observe(
    *,
    command_sender: BlenderCommandSender,
    cam_name: str,
    azimuth: float,
    elevation: float,
    object_names: list[str],
) -> dict[str, Any] | None:
    temp_path = os.path.join(
        tempfile.gettempdir(),
        f"scene_cam_{cam_name}_{os.getpid()}_{int(time.time() * 1000)}.png",
    )
    result = command_sender(
        "camera_observe",
        {
            "object_names": object_names,
            "mode": "single_view",
            "focal_length": _SCENE_CAMERA_FOCAL_MM,
            "azimuth": azimuth,
            "elevation": elevation,
            "reuse_cameras": True,
            "camera_name": cam_name,
            "camera_kind": "scene_level",
            "filepath": temp_path,
        },
    )
    if not isinstance(result, dict) or not result.get("success", False):
        logger.warning("Scene camera %s render failed: %s", cam_name, result)
        return None
    return result


def _render_scene_camera_with_pose(
    *,
    command_sender: BlenderCommandSender,
    cam_name: str,
    location: list[float],
    center: list[float],
    object_names: list[str],
) -> dict[str, Any] | None:
    rotation_euler = _look_at_rotation_euler(location, center)
    temp_path = os.path.join(
        tempfile.gettempdir(),
        f"scene_cam_{cam_name}_{os.getpid()}_{int(time.time() * 1000)}.png",
    )
    result = command_sender(
        "camera_set_pose",
        {
            "location": location,
            "rotation_euler": rotation_euler,
            "focal_mm": _SCENE_CAMERA_FOCAL_MM,
            "mode": "rgb",
            "object_names": object_names,
            "camera_name": cam_name,
            "camera_kind": "scene_level",
            "filepath": temp_path,
            "render_output": True,
        },
    )
    if not isinstance(result, dict) or not result.get("success", False):
        logger.warning("Scene camera %s pose render failed: %s", cam_name, result)
        return None
    return result


def update_scene_cameras(
    *,
    thread_id: str = "unknown",
    send_blender_command: BlenderCommandSender | None = None,
    use_direct_pose: bool = False,
) -> dict[str, Any]:
    """Render the scene from diagnostic cameras (2 diagonal + top-down views).

    This is an **internal** helper — NOT exposed as an MCP tool.  It is
    called by the ``scene_observe`` graph node after scene-mutating tools.

    Returns a dict with keys:
        success, scene_bbox, cameras (list of per-camera dicts with
        camera_name, location, filepath, image_url), and image_urls.
    """
    command_sender = send_blender_command
    if command_sender is None:
        blender = runtime.get_blender_connection(logger)
        command_sender = blender.send_command

    # 1. Gather scene info to compute union AABB
    scene_info = command_sender("get_scene_info", {})
    if not scene_info or not scene_info.get("success", True):
        return {"success": False, "error": "get_scene_info failed"}

    aabb = _compute_union_aabb(scene_info)
    if aabb is None:
        return {"success": False, "error": "No mesh objects with bounding boxes found"}

    center = aabb["center"]
    dimensions = aabb["dimensions"]
    distance = _fov_aware_distance(dimensions)
    object_names = [
        name
        for name in aabb.get("object_names", [])
        if isinstance(name, str) and name.strip()
    ]

    excluded_names = aabb.get("excluded_object_names")
    if isinstance(excluded_names, list) and excluded_names:
        logger.info(
            "Scene camera bbox outlier trim applied: excluded=%s",
            excluded_names,
        )

    # 2. Place cameras and render
    cameras: list[dict[str, Any]] = []
    image_paths: list[str] = []

    for cam_name, azimuth, elevation in _SCENE_CAMERA_VIEW_CONFIGS:
        location = _spherical_to_cartesian(center, distance, azimuth, elevation)

        try:
            if use_direct_pose:
                result = _render_scene_camera_with_pose(
                    command_sender=command_sender,
                    cam_name=cam_name,
                    location=location,
                    center=center,
                    object_names=object_names,
                )
                if result is None:
                    result = _render_scene_camera_with_observe(
                        command_sender=command_sender,
                        cam_name=cam_name,
                        azimuth=azimuth,
                        elevation=elevation,
                        object_names=object_names,
                    )
            else:
                result = _render_scene_camera_with_observe(
                    command_sender=command_sender,
                    cam_name=cam_name,
                    azimuth=azimuth,
                    elevation=elevation,
                    object_names=object_names,
                )
        except Exception as exc:
            logger.warning("Scene camera %s render raised error: %s", cam_name, exc)
            result = None

        if result is None:
            continue

        filepath = result.get("filepath")
        if not isinstance(filepath, str) or not filepath:
            logger.warning("Scene camera %s returned invalid filepath: %s", cam_name, result)
            continue
        if not os.path.exists(filepath):
            logger.warning("Scene camera %s output file missing: %s", cam_name, filepath)
            continue
        image_url = process_and_save_render(filepath, thread_id, cam_name, logger=logger)
        try:
            os.remove(filepath)
        except Exception:
            pass

        cam_entry: dict[str, Any] = {
            "camera_name": cam_name,
            "location": location,
            "rotation_euler": _look_at_rotation_euler(location, center),
            "focal_mm": _SCENE_CAMERA_FOCAL_MM,
            "azimuth": azimuth,
            "elevation": elevation,
            "filepath": filepath,
            "image_url": image_url,
        }
        cameras.append(cam_entry)
        image_paths.append(image_url)

    if not cameras:
        return {"success": False, "error": "All scene camera renders failed"}

    return {
        "success": True,
        "scene_bbox": {"center": center, "dimensions": dimensions},
        "cameras": cameras,
        "image_urls": image_paths,
    }


def observe_scene_global(ctx: Context) -> CallToolResult:
    """Capture scene-wide 3-view observation using diagnostic cameras.

    Use this when the agent needs a global understanding of composition, or when
    local renders appear unreliable (for example, blank/black outputs).
    """
    thread_id = _extract_thread_id(ctx)
    try:
        try:
            result = update_scene_cameras(
                thread_id=thread_id,
                use_direct_pose=True,
            )
        except TypeError as exc:
            if "use_direct_pose" not in str(exc):
                raise
            result = update_scene_cameras(thread_id=thread_id)
    except Exception as exc:
        logger.error("Error running global scene observation: %s", str(exc))
        raise Exception(f"Global scene observation failed: {str(exc)}")

    if not result.get("success"):
        error_message = str(result.get("error", "unknown error"))
        return CallToolResult(
            content=[
                {
                    "type": "text",
                    "text": (
                        "Global scene observation failed. "
                        f"Reason: {error_message}. "
                        "Try get_scene_info() first, then mutate or import scene objects before retrying."
                    ),
                }
            ],
            isError=False,
        )

    cameras = result.get("cameras", [])
    image_entries: list[tuple[str, str]] = []
    for camera in cameras:
        if not isinstance(camera, dict):
            continue
        camera_name = camera.get("camera_name")
        image_url = camera.get("image_url")
        if isinstance(camera_name, str) and camera_name and isinstance(image_url, str) and image_url:
            image_entries.append((camera_name, image_url))

    if not image_entries:
        return CallToolResult(
            content=[
                {
                    "type": "text",
                    "text": (
                        "Global scene observation completed, but no render images were produced. "
                        "Please retry after confirming scene objects exist."
                    ),
                }
            ],
            isError=False,
        )

    scene_bbox = result.get("scene_bbox")
    lines: list[str] = [f"Global scene observation ({len(SCENE_CAMERA_NAMES)}-view diagnostic cameras):"]
    if isinstance(scene_bbox, dict):
        center = scene_bbox.get("center")
        dimensions = scene_bbox.get("dimensions")
        if isinstance(center, list) and isinstance(dimensions, list):
            lines.append(f"- scene_bbox.center: {center}")
            lines.append(f"- scene_bbox.dimensions: {dimensions}")

    grid_url = _build_scene_grid_image(image_entries, thread_id=thread_id)
    lines.append("")
    if isinstance(grid_url, str) and grid_url:
        # Keep the grid image first so downstream markdown extraction uses it.
        lines.append(f"Grid overview: ![{_SCENE_GRID_CAMERA_NAME}]({grid_url})")
        lines.append("")
    lines.append("Captured views:")
    for camera_name, image_url in image_entries:
        lines.append(f"- {camera_name}: ![{camera_name}]({image_url})")

    return CallToolResult(
        content=[{"type": "text", "text": "\n".join(lines)}],
        isError=False,
    )


def _extract_thread_id(ctx: Context) -> str:
    thread_id = "unknown"
    if hasattr(ctx, "request_context") and ctx.request_context:
        if hasattr(ctx.request_context, "get"):
            thread_id = ctx.request_context.get("thread_id", "unknown")
        elif isinstance(ctx.request_context, dict):
            thread_id = ctx.request_context.get("thread_id", "unknown")
    return thread_id


def _resolve_render_url_to_local_path(image_url: str) -> str | None:
    if not isinstance(image_url, str):
        return None
    normalized = image_url.strip()
    if not normalized:
        return None
    if normalized.startswith("file://"):
        normalized = normalized.replace("file://", "", 1)
    if os.path.exists(normalized):
        return normalized

    parsed = urlparse(normalized)
    render_path = parsed.path if parsed.scheme and parsed.netloc else normalized
    if not render_path.startswith("/renders/"):
        return None
    filename = unquote(render_path.replace("/renders/", "", 1).strip("/"))
    if not filename:
        return None
    candidate = RENDERS_DIR / filename
    if candidate.exists():
        return str(candidate)
    return None


def _build_scene_grid_image(
    image_entries: list[tuple[str, str]],
    *,
    thread_id: str,
) -> str | None:
    if len(image_entries) < 2:
        return None

    local_entries: list[tuple[str, str]] = []
    for camera_name, image_url in image_entries:
        local_path = _resolve_render_url_to_local_path(image_url)
        if not local_path:
            return None
        local_entries.append((camera_name, local_path))

    opened_images: list[PILImage.Image] = []
    temp_grid_path: str | None = None
    try:
        for _camera_name, local_path in local_entries:
            opened_images.append(PILImage.open(local_path).convert("RGB"))
        if not opened_images:
            return None

        cell_width = max(image.width for image in opened_images)
        cell_height = max(image.height for image in opened_images)
        image_count = len(opened_images)
        columns = 2 if image_count <= 4 else 3
        rows = math.ceil(image_count / columns)
        canvas = PILImage.new("RGB", (cell_width * columns, cell_height * rows), color=(24, 24, 24))

        for index, image in enumerate(opened_images):
            grid_x = index % columns
            grid_y = index // columns
            tile = image
            if tile.size != (cell_width, cell_height):
                tile = tile.resize((cell_width, cell_height), PILImage.Resampling.LANCZOS)
            canvas.paste(tile, (grid_x * cell_width, grid_y * cell_height))

        with tempfile.NamedTemporaryFile(
            suffix="_scene_global_grid.jpg",
            delete=False,
        ) as tmp_file:
            temp_grid_path = tmp_file.name
        canvas.save(temp_grid_path, "JPEG", quality=88, optimize=True)
        return process_and_save_render(
            temp_grid_path,
            thread_id,
            _SCENE_GRID_CAMERA_NAME,
            logger=logger,
        )
    except Exception as exc:
        logger.warning("Failed to build scene global grid image: %s", exc)
        return None
    finally:
        for image in opened_images:
            try:
                image.close()
            except Exception:
                pass
        if temp_grid_path and os.path.exists(temp_grid_path):
            try:
                os.remove(temp_grid_path)
            except OSError:
                pass


def _render_result_to_markdown(
    *,
    filepath: str,
    thread_id: str,
    camera_name: str,
    alt_text: str,
) -> CallToolResult:
    if not os.path.exists(filepath):
        raise Exception(f"Rendered file not found: {filepath}")
    image_url = process_and_save_render(filepath, thread_id, camera_name, logger=logger)
    try:
        os.remove(filepath)
    except Exception:
        pass
    logger.info("Render available at: %s", image_url)
    markdown_image = f"![{alt_text}]({image_url})"
    return CallToolResult(content=[{"type": "text", "text": markdown_image}], isError=False)


def _is_no_valid_mesh_error(error_text: str) -> bool:
    normalized = (error_text or "").lower()
    return "no valid mesh objects found" in normalized


def _render_all_meshes_fallback(
    *,
    ctx: Context,
    blender,
    mode: str,
    focal_length: str,
    azimuth: float,
    elevation: float,
) -> CallToolResult | None:
    temp_path = os.path.join(
        tempfile.gettempdir(), f"blender_render_fallback_{os.getpid()}_{int(time.time())}.png"
    )
    result = blender.send_command(
        "camera_observe",
        {
            "object_names": [],
            "mode": "single_view",
            "focal_length": focal_length,
            "azimuth": azimuth,
            "elevation": elevation,
            "reuse_cameras": True,
            "filepath": temp_path,
        },
    )
    if not result.get("success"):
        return None
    filepath = result["filepath"]
    thread_id = _extract_thread_id(ctx)
    camera_name = result.get("camera", "auto")
    return _render_result_to_markdown(
        filepath=filepath,
        thread_id=thread_id,
        camera_name=camera_name,
        alt_text=f"Fallback render ({mode})",
    )


def render_from_objects(
    ctx: Context,
    object_names: list[str],
    mode: str = "rgb",
    focal_length: str = "normal",
    azimuth: float = 45,
    elevation: float = 30,
) -> CallToolResult:
    """Render image by auto-creating camera and return markdown image link."""
    blender = None
    try:
        blender = runtime.get_blender_connection(logger)
        thread_id = _extract_thread_id(ctx)
        temp_path = os.path.join(
            tempfile.gettempdir(), f"blender_render_{os.getpid()}_{int(time.time())}.png"
        )
        result = blender.send_command(
            "render_from_objects",
            {
                "object_names": object_names,
                "mode": mode,
                "focal_length": focal_length,
                "azimuth": azimuth,
                "elevation": elevation,
                "filepath": temp_path,
            },
        )
        if not result.get("success"):
            raise Exception("Render failed")
        filepath = result["filepath"]
        camera_name = result.get("camera", "auto")
        objects_str = ", ".join(object_names)
        return _render_result_to_markdown(
            filepath=filepath,
            thread_id=thread_id,
            camera_name=camera_name,
            alt_text=f"Render of {objects_str}",
        )
    except Exception as exc:
        error_text = str(exc)
        if _is_no_valid_mesh_error(error_text):
            logger.warning(
                "render_from_objects fallback triggered for target objects %s: %s",
                object_names,
                error_text,
            )
            if blender is not None:
                try:
                    fallback_result = _render_all_meshes_fallback(
                        ctx=ctx,
                        blender=blender,
                        mode=mode,
                        focal_length=focal_length,
                        azimuth=azimuth,
                        elevation=elevation,
                    )
                    if fallback_result is not None:
                        return fallback_result
                except Exception as fallback_exc:
                    logger.warning("render_from_objects fallback failed: %s", str(fallback_exc))
            fallback_note = (
                "Render skipped: no valid mesh objects were found for this request. "
                "Try `get_scene_info` to inspect current object names."
            )
            return CallToolResult(
                content=[{"type": "text", "text": fallback_note}],
                isError=False,
            )
        logger.error("Error rendering from objects: %s", error_text)
        raise Exception(f"Render failed: {str(exc)}")


def render_from_camera(
    ctx: Context,
    camera_name: str,
    object_names: Optional[list[str]] = None,
    mode: str = "rgb",
) -> CallToolResult:
    """Render from an existing camera and return markdown image link."""
    try:
        blender = runtime.get_blender_connection(logger)
        temp_path = os.path.join(
            tempfile.gettempdir(), f"blender_render_{os.getpid()}_{int(time.time())}.png"
        )
        result = blender.send_command(
            "render_from_camera",
            {
                "camera_name": camera_name,
                "object_names": object_names,
                "mode": mode,
                "filepath": temp_path,
            },
        )
        if not result.get("success"):
            raise Exception("Render failed")
        filepath = result["filepath"]
        thread_id = _extract_thread_id(ctx)
        return _render_result_to_markdown(
            filepath=filepath,
            thread_id=thread_id,
            camera_name=camera_name,
            alt_text=f"Render from {camera_name}",
        )
    except Exception as exc:
        logger.error("Error rendering from camera: %s", str(exc))
        raise Exception(f"Render failed: {str(exc)}")


def camera_observe(
    ctx: Context,
    object_names: list[str],
    mode: str = "multi_view",
    focal_length: str = "normal",
    azimuth: float = 45,
    elevation: float = 30,
    reuse_cameras: bool = True,
) -> CallToolResult:
    """Observe objects with single or multi-view camera strategy."""
    try:
        blender = runtime.get_blender_connection(logger)
        temp_path = os.path.join(
            tempfile.gettempdir(), f"blender_observe_{os.getpid()}_{int(time.time())}.png"
        )
        result = blender.send_command(
            "camera_observe",
            {
                "object_names": object_names,
                "mode": mode,
                "focal_length": focal_length,
                "azimuth": azimuth,
                "elevation": elevation,
                "reuse_cameras": reuse_cameras,
                "filepath": temp_path,
            },
        )
        if not result.get("success"):
            raise Exception("Camera observe failed")
        filepath = result["filepath"]
        thread_id = _extract_thread_id(ctx)
        camera_name = result.get("camera", "observe")
        return _render_result_to_markdown(
            filepath=filepath,
            thread_id=thread_id,
            camera_name=camera_name,
            alt_text=f"Observation view ({mode})",
        )
    except Exception as exc:
        logger.error("Error observing scene: %s", str(exc))
        raise Exception(f"Camera observe failed: {str(exc)}")


def camera_act(
    ctx: Context,
    action: str = "focus",
    object_names: Optional[list[str]] = None,
    direction: Optional[str] = None,
    step_scale: float = 1.0,
    keep_distance: bool = True,
    mode: str = "rgb",
) -> CallToolResult:
    """Actively control camera with focus/move/zoom and render."""
    try:
        blender = runtime.get_blender_connection(logger)
        temp_path = os.path.join(
            tempfile.gettempdir(), f"blender_camera_act_{os.getpid()}_{int(time.time())}.png"
        )
        result = blender.send_command(
            "camera_act",
            {
                "action": action,
                "object_names": object_names or [],
                "direction": direction,
                "step_scale": step_scale,
                "keep_distance": keep_distance,
                "mode": mode,
                "filepath": temp_path,
            },
        )
        if not result.get("success"):
            raise Exception("Camera act failed")
        filepath = result["filepath"]
        thread_id = _extract_thread_id(ctx)
        camera_name = result.get("camera", "camera_act")
        return _render_result_to_markdown(
            filepath=filepath,
            thread_id=thread_id,
            camera_name=camera_name,
            alt_text=f"Camera action ({action})",
        )
    except Exception as exc:
        logger.error("Error running camera action: %s", str(exc))
        raise Exception(f"Camera action failed: {str(exc)}")


def camera_set_pose(
    ctx: Context,
    location: list[float],
    rotation_euler: list[float],
    focal_mm: float = 50.0,
    mode: str = "rgb",
    object_names: Optional[list[str]] = None,
) -> CallToolResult:
    """Set absolute camera pose and render from that viewpoint."""
    try:
        blender = runtime.get_blender_connection(logger)
        temp_path = os.path.join(
            tempfile.gettempdir(), f"blender_camera_pose_{os.getpid()}_{int(time.time())}.png"
        )
        result = blender.send_command(
            "camera_set_pose",
            {
                "location": location,
                "rotation_euler": rotation_euler,
                "focal_mm": focal_mm,
                "mode": mode,
                "object_names": object_names or [],
                "filepath": temp_path,
            },
        )
        if not result.get("success"):
            raise Exception("Camera set pose failed")
        filepath = result["filepath"]
        thread_id = _extract_thread_id(ctx)
        camera_name = result.get("camera", "camera_set_pose")
        return _render_result_to_markdown(
            filepath=filepath,
            thread_id=thread_id,
            camera_name=camera_name,
            alt_text="Camera pose render",
        )
    except Exception as exc:
        logger.error("Error setting camera pose: %s", str(exc))
        raise Exception(f"Camera set pose failed: {str(exc)}")
