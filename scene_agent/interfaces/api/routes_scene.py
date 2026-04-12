"""API routes."""
import asyncio
from importlib import import_module
import os
import re
import tempfile
import time
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from scene_agent.blender.session_manager import SessionResourceError, get_session_manager
from scene_agent.config import get_settings
from scene_agent.memory.scene_memory import SceneMemory
from scene_agent.utils.diagnostics import elapsed_ms, new_request_id, start_timer
from scene_agent.utils.logging import log_event
from scene_agent.utils.rendering import process_and_save_render

from .models import (
    AddPrimitiveRequest,
    AddPrimitiveResponse,
    BlendFileEntry,
    BlendFileListResponse,
    DeleteSceneObjectRequest,
    DeleteSceneObjectResponse,
    SceneArtifactManifestResponse,
    TransformSceneObjectRequest,
    TransformSceneObjectResponse,
)
from .shared import (
    blend_file_category,
    build_scene_gltf_export_code,
    load_thread_scene_artifact_manifest,
    build_headless_diagnostics,
    claim_or_proxy_request,
    ensure_scene_agent_object_metadata_sync,
    execute_headless_export_code,
    headless_timeout_seconds_for_session,
    persist_thread_scene_artifacts_sync,
    resolve_thread_artifact_gltf_path,
    resolve_thread_artifact_renders_dir,
    render_scene_level_views,
    resolve_thread_storage_dir,
    restart_headless_session_after_timeout,
    set_owner_headers,
)


def resolve_api_module():
    return import_module("scene_agent.interfaces.api")


def send_blender_command_sync(command_type, params=None, thread_id=None):
    api_module = resolve_api_module()
    return api_module.send_blender_command_sync(command_type, params, thread_id)


_PRIMITIVE_DISPLAY_NAMES: dict[str, str] = {
    "cube": "Cube",
    "sphere": "Sphere",
    "cylinder": "Cylinder",
    "plane": "Plane",
    "cone": "Cone",
    "torus": "Torus",
    "icosphere": "Icosphere",
}
_CREATED_PRIMITIVE_MARKER = re.compile(r"CREATED:(?P<name>[^\n|]+)\|(?P<object_id>[^\n]+)")


def _coerce_primitive_location(location: list[float]) -> tuple[float, float, float]:
    if len(location) != 3:
        raise HTTPException(status_code=422, detail="Primitive location must contain exactly three coordinates.")
    return (float(location[0]), float(location[1]), float(location[2]))


def _build_primitive_op_call(
    primitive_type: str,
    location: tuple[float, float, float],
    size: float,
) -> str:
    location_repr = repr(location)
    if primitive_type == "cube":
        return f"bpy.ops.mesh.primitive_cube_add(size={size!r}, location={location_repr})"
    if primitive_type == "sphere":
        return f"bpy.ops.mesh.primitive_uv_sphere_add(radius={size!r}, location={location_repr})"
    if primitive_type == "cylinder":
        return (
            "bpy.ops.mesh.primitive_cylinder_add("
            f"radius={size!r}, depth={(size * 2.0)!r}, location={location_repr})"
        )
    if primitive_type == "plane":
        return f"bpy.ops.mesh.primitive_plane_add(size={size!r}, location={location_repr})"
    if primitive_type == "cone":
        return (
            "bpy.ops.mesh.primitive_cone_add("
            f"radius1={size!r}, depth={(size * 2.0)!r}, location={location_repr})"
        )
    if primitive_type == "torus":
        return (
            "bpy.ops.mesh.primitive_torus_add("
            f"major_radius={size!r}, minor_radius={max(size * 0.3, 0.1)!r}, location={location_repr})"
        )
    if primitive_type == "icosphere":
        return f"bpy.ops.mesh.primitive_ico_sphere_add(radius={size!r}, location={location_repr})"
    raise HTTPException(status_code=422, detail=f"Unsupported primitive type '{primitive_type}'.")


def _build_primitive_code(
    primitive_type: str,
    location: list[float],
    size: float,
    backend_object_id: str,
) -> str:
    display_name = _PRIMITIVE_DISPLAY_NAMES[primitive_type]
    op_call = _build_primitive_op_call(
        primitive_type,
        _coerce_primitive_location(location),
        float(size),
    )
    return "\n".join(
        [
            "import bpy",
            op_call,
            "_obj = bpy.context.active_object",
            'if _obj is None: raise RuntimeError("Primitive creation did not produce an active object.")',
            f"_obj.name = {display_name!r}",
            f'_obj["scene_agent_object_id"] = {backend_object_id!r}',
            '_obj["scene_agent_object_name"] = _obj.name',
            'print("CREATED:" + _obj.name + "|" + str(_obj.get("scene_agent_object_id", "")))',
            "",
        ]
    )


def _parse_created_primitive_marker(stdout: str | None) -> tuple[str | None, str | None]:
    if not isinstance(stdout, str) or not stdout:
        return None, None
    match = _CREATED_PRIMITIVE_MARKER.search(stdout)
    if not match:
        return None, None
    name = match.group("name").strip() or None
    object_id = match.group("object_id").strip() or None
    return name, object_id


router = APIRouter()
@router.get("/scene/{thread_id}")
async def get_scene(thread_id: str, request: Request, response: Response):
    """
    Get current scene state for a thread.
    
    Args:
        thread_id: Thread identifier
        
    Returns:
        Scene objects and metadata
    """
    resolution, proxied = await claim_or_proxy_request(request=request, thread_id=thread_id)
    if proxied is not None:
        return proxied
    try:
        settings = get_settings()
        if settings.blender_mode == "headless":
            manager = get_session_manager()
            session = manager.ensure(thread_id, "headless")
            request_timeout_seconds = headless_timeout_seconds_for_session(settings, session)
            request_id = new_request_id(thread_id)
            start_time = start_timer()
            try:
                scene_info = await asyncio.wait_for(
                    asyncio.to_thread(send_blender_command_sync, "get_scene_info", None, thread_id),
                    timeout=request_timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                elapsed_value = elapsed_ms(start_time)
                diagnostics = build_headless_diagnostics(
                    session=session,
                    request_id=request_id,
                    elapsed_ms_value=elapsed_value,
                    status="timeout",
                    target_ms=int(request_timeout_seconds * 1000),
                )
                log_event("error", "headless_scene_timeout", diagnostics)
                restart_headless_session_after_timeout(thread_id)
                raise HTTPException(
                    status_code=504,
                    detail={"error": "Headless scene request timed out.", **diagnostics},
                ) from exc
            except SessionResourceError as exc:
                raise HTTPException(status_code=503, detail=exc.detail) from exc
            except Exception as exc:
                elapsed_value = elapsed_ms(start_time)
                diagnostics = build_headless_diagnostics(
                    session=session,
                    request_id=request_id,
                    elapsed_ms_value=elapsed_value,
                    status="error",
                    target_ms=int(request_timeout_seconds * 1000),
                )
                log_event("error", "headless_scene_failed", {**diagnostics, "error": str(exc)})
                raise HTTPException(
                    status_code=500,
                    detail={"error": str(exc), **diagnostics},
                ) from exc
            else:
                elapsed_value = elapsed_ms(start_time)
                diagnostics = build_headless_diagnostics(
                    session=session,
                    request_id=request_id,
                    elapsed_ms_value=elapsed_value,
                    status="ok",
                    target_ms=int(request_timeout_seconds * 1000),
                )
                log_event("info", "headless_scene_ok", diagnostics)
        else:
            scene_info = await asyncio.to_thread(send_blender_command_sync, "get_scene_info", None, thread_id)
        scene_objects = SceneMemory.parse_scene_info(scene_info)
        objects = scene_info.get("objects", []) if isinstance(scene_info, dict) else []
        cameras = [obj.get("name") for obj in objects if obj.get("type") == "CAMERA" and obj.get("name")]
        payload = {
            "thread_id": thread_id,
            "scene_objects": scene_objects,
            "persistent_cameras": cameras,
            "iteration_count": 0
        }
        set_owner_headers(response, resolution)
        return payload
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        if isinstance(e, SessionResourceError):
            raise HTTPException(status_code=503, detail=e.detail) from e
        settings = get_settings()
        if settings.blender_mode == "local-client":
            raise HTTPException(
                status_code=503,
                detail="Blender client not connected. Start the Blender addon or enable headless mode."
            ) from e
        raise

@router.get("/scene/{thread_id}/renders")
async def get_scene_renders(
    thread_id: str,
    request: Request,
    response: Response,
    mode: str = "rgb",
    include_local_work: bool = False,
):
    """
    Render all cameras in the current Blender scene and return processed image URLs.
    """
    resolution, proxied = await claim_or_proxy_request(request=request, thread_id=thread_id)
    if proxied is not None:
        return proxied
    try:
        settings = get_settings()
        diagnostics = None
        request_timeout_seconds: float | None = None
        if settings.blender_mode == "headless":
            manager = get_session_manager()
            session = manager.ensure(thread_id, "headless")
            request_timeout_seconds = headless_timeout_seconds_for_session(settings, session)
            request_id = new_request_id(thread_id)
            start_time = start_timer()
            try:
                scene_info = await asyncio.wait_for(
                    asyncio.to_thread(send_blender_command_sync, "get_scene_info", None, thread_id),
                    timeout=request_timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                elapsed_value = elapsed_ms(start_time)
                diagnostics = build_headless_diagnostics(
                    session=session,
                    request_id=request_id,
                    elapsed_ms_value=elapsed_value,
                    status="timeout",
                    target_ms=int(request_timeout_seconds * 1000),
                )
                log_event("error", "headless_renders_scene_timeout", diagnostics)
                restart_headless_session_after_timeout(thread_id)
                raise HTTPException(
                    status_code=504,
                    detail={"error": "Headless render request timed out.", **diagnostics},
                ) from exc
            except SessionResourceError as exc:
                raise HTTPException(status_code=503, detail=exc.detail) from exc
            except Exception as exc:
                elapsed_value = elapsed_ms(start_time)
                diagnostics = build_headless_diagnostics(
                    session=session,
                    request_id=request_id,
                    elapsed_ms_value=elapsed_value,
                    status="error",
                    target_ms=int(request_timeout_seconds * 1000),
                )
                log_event("error", "headless_renders_scene_failed", {**diagnostics, "error": str(exc)})
                raise HTTPException(
                    status_code=500,
                    detail={"error": str(exc), **diagnostics},
                ) from exc
        else:
            scene_info = await asyncio.to_thread(send_blender_command_sync, "get_scene_info", None, thread_id)

        start_time = start_timer()
        renders: list[dict[str, str]] = []
        if mode == "rgb":
            renders = await render_scene_level_views(
                thread_id=thread_id,
                is_headless=settings.blender_mode == "headless",
                request_timeout_seconds=request_timeout_seconds,
            )

        # Backward-compatible fallback: if scene-level strategy returns no output,
        # render from any existing camera objects in scene info.
        if not renders:
            objects = scene_info.get("objects", []) if isinstance(scene_info, dict) else []
            cameras = [obj.get("name") for obj in objects if obj.get("type") == "CAMERA"]
            for camera_name in cameras:
                if not camera_name:
                    continue
                temp_path = os.path.join(
                    tempfile.gettempdir(),
                    f"blender_render_{camera_name}_{int(time.time() * 1000)}.png"
                )
                render_call = asyncio.to_thread(
                    send_blender_command_sync,
                    "render_from_camera",
                    {
                        "camera_name": camera_name,
                        "object_names": None,
                        "mode": mode,
                        "filepath": temp_path
                    },
                    thread_id
                )
                if settings.blender_mode == "headless":
                    try:
                        result = await asyncio.wait_for(render_call, timeout=request_timeout_seconds)
                    except asyncio.TimeoutError as exc:
                        elapsed_value = elapsed_ms(start_time)
                        diagnostics = build_headless_diagnostics(
                            session=session,
                            request_id=request_id,
                            elapsed_ms_value=elapsed_value,
                            status="timeout",
                            target_ms=int(request_timeout_seconds * 1000),
                        )
                        log_event(
                            "error",
                            "headless_renders_timeout",
                            {**diagnostics, "camera_name": camera_name},
                        )
                        restart_headless_session_after_timeout(thread_id)
                        raise HTTPException(
                            status_code=504,
                            detail={"error": "Headless render request timed out.", **diagnostics},
                        ) from exc
                else:
                    result = await render_call
                filepath = result.get("filepath") or temp_path
                if not os.path.exists(filepath):
                    continue
                image_url = process_and_save_render(
                    filepath,
                    thread_id,
                    camera_name,
                    log_event=log_event,
                )
                try:
                    os.remove(filepath)
                except OSError:
                    pass
                renders.append({
                    "camera_name": camera_name,
                    "image_url": image_url
                })

        if include_local_work:
            existing_camera_names = {
                entry.get("camera_name")
                for entry in renders
                if isinstance(entry, dict) and isinstance(entry.get("camera_name"), str)
            }
            local_work_camera_names: list[str] = []
            list_call = asyncio.to_thread(
                send_blender_command_sync,
                "get_camera_manager_cameras",
                {
                    "only_local": True,
                    "include_invalid": False,
                },
                thread_id,
            )
            try:
                if settings.blender_mode == "headless":
                    camera_listing = await asyncio.wait_for(list_call, timeout=request_timeout_seconds)
                else:
                    camera_listing = await list_call
            except asyncio.TimeoutError as exc:
                elapsed_value = elapsed_ms(start_time)
                diagnostics = build_headless_diagnostics(
                    session=session,
                    request_id=request_id,
                    elapsed_ms_value=elapsed_value,
                    status="timeout",
                    target_ms=int(request_timeout_seconds * 1000),
                )
                log_event("error", "headless_local_work_camera_list_timeout", diagnostics)
                restart_headless_session_after_timeout(thread_id)
                raise HTTPException(
                    status_code=504,
                    detail={"error": "Headless local-work camera listing timed out.", **diagnostics},
                ) from exc
            except Exception as exc:
                log_event(
                    "warning",
                    "local_work_camera_list_failed",
                    {"thread_id": thread_id, "error": str(exc)},
                )
                camera_listing = {}

            if isinstance(camera_listing, dict):
                raw_camera_entries = camera_listing.get("cameras")
                if isinstance(raw_camera_entries, list):
                    for entry in raw_camera_entries:
                        if not isinstance(entry, dict):
                            continue
                        camera_name = entry.get("camera_name")
                        camera_kind = entry.get("camera_kind")
                        if (
                            isinstance(camera_name, str)
                            and camera_name
                            and camera_name not in existing_camera_names
                            and camera_kind == "local_work"
                        ):
                            local_work_camera_names.append(camera_name)

            for camera_name in local_work_camera_names:
                temp_path = os.path.join(
                    tempfile.gettempdir(),
                    f"blender_render_local_work_{camera_name}_{int(time.time() * 1000)}.png"
                )
                render_call = asyncio.to_thread(
                    send_blender_command_sync,
                    "render_from_camera",
                    {
                        "camera_name": camera_name,
                        "object_names": None,
                        "mode": mode,
                        "filepath": temp_path
                    },
                    thread_id
                )
                if settings.blender_mode == "headless":
                    try:
                        result = await asyncio.wait_for(render_call, timeout=request_timeout_seconds)
                    except asyncio.TimeoutError as exc:
                        elapsed_value = elapsed_ms(start_time)
                        diagnostics = build_headless_diagnostics(
                            session=session,
                            request_id=request_id,
                            elapsed_ms_value=elapsed_value,
                            status="timeout",
                            target_ms=int(request_timeout_seconds * 1000),
                        )
                        log_event(
                            "error",
                            "headless_local_work_render_timeout",
                            {**diagnostics, "camera_name": camera_name},
                        )
                        restart_headless_session_after_timeout(thread_id)
                        raise HTTPException(
                            status_code=504,
                            detail={"error": "Headless local-work camera render timed out.", **diagnostics},
                        ) from exc
                    except Exception as exc:
                        log_event(
                            "warning",
                            "local_work_render_failed",
                            {"thread_id": thread_id, "camera_name": camera_name, "error": str(exc)},
                        )
                        continue
                else:
                    try:
                        result = await render_call
                    except Exception as exc:
                        log_event(
                            "warning",
                            "local_work_render_failed",
                            {"thread_id": thread_id, "camera_name": camera_name, "error": str(exc)},
                        )
                        continue

                filepath = result.get("filepath") or temp_path
                if not os.path.exists(filepath):
                    continue
                image_url = process_and_save_render(
                    filepath,
                    thread_id,
                    camera_name,
                    log_event=log_event,
                )
                try:
                    os.remove(filepath)
                except OSError:
                    pass
                renders.append({
                    "camera_name": camera_name,
                    "image_url": image_url,
                })
                existing_camera_names.add(camera_name)

        if settings.blender_mode == "headless":
            elapsed_value = elapsed_ms(start_time)
            diagnostics = build_headless_diagnostics(
                session=session,
                request_id=request_id,
                elapsed_ms_value=elapsed_value,
                status="ok",
                target_ms=int(request_timeout_seconds * 1000),
            )
            log_event("info", "headless_renders_ok", diagnostics)
        payload = {
            "thread_id": thread_id,
            "renders": renders,
            "diagnostics": diagnostics,
            "include_local_work": bool(include_local_work),
        }
        set_owner_headers(response, resolution)
        return payload
    except Exception as e:
        import traceback
        traceback.print_exc()
        if isinstance(e, HTTPException):
            raise
        if isinstance(e, SessionResourceError):
            raise HTTPException(status_code=503, detail=e.detail) from e
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/scene/{thread_id}/gltf")
async def get_scene_gltf(thread_id: str, request: Request):
    """
    Export current Blender scene to GLB and return the binary.
    """
    resolution, proxied = await claim_or_proxy_request(request=request, thread_id=thread_id)
    if proxied is not None:
        return proxied
    try:
        settings = get_settings()
        temp_path = os.path.join(
            tempfile.gettempdir(),
            f"scene_{thread_id}_{int(time.time() * 1000)}.glb"
        )
        ensure_scene_agent_object_metadata_sync(thread_id)
        export_code = build_scene_gltf_export_code(temp_path)
        if settings.blender_mode == "headless":
            manager = get_session_manager()
            session = manager.ensure(thread_id, "headless")
            request_timeout_seconds = headless_timeout_seconds_for_session(settings, session)
            await execute_headless_export_code(
                thread_id=thread_id,
                export_code=export_code,
                execute_code_params={"validate_scene": False},
                timeout_seconds=request_timeout_seconds,
                timeout_error_message="Headless GLTF export timed out.",
                timeout_event_name="headless_gltf_timeout",
                failed_event_name="headless_gltf_failed",
                ok_event_name="headless_gltf_ok",
                restart_on_timeout=True,
            )
        else:
            await asyncio.to_thread(
                send_blender_command_sync,
                "execute_code",
                {
                    "code": export_code,
                    "validate_scene": False,
                },
                thread_id,
            )

        # Wait for file to be written with retries
        max_retries = 10
        retry_delay = 0.2
        temp_dir = tempfile.gettempdir()
        log_event("info", "gltf_export_waiting", {
            "thread_id": thread_id,
            "temp_path": temp_path,
            "temp_dir": temp_dir,
            "max_retries": max_retries
        })
        
        for attempt in range(max_retries):
            if os.path.exists(temp_path):
                file_size = os.path.getsize(temp_path)
                if file_size > 0:
                    log_event("info", "gltf_export_file_ready", {
                        "thread_id": thread_id,
                        "attempt": attempt + 1,
                        "file_size": file_size
                    })
                    break
                else:
                    log_event("warning", "gltf_export_file_empty", {
                        "thread_id": thread_id,
                        "attempt": attempt + 1
                    })
            else:
                log_event("debug", "gltf_export_file_not_found", {
                    "thread_id": thread_id,
                    "attempt": attempt + 1
                })
            
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
        
        if not os.path.exists(temp_path):
            log_event("error", "gltf_export_failed_not_found", {
                "thread_id": thread_id,
                "temp_path": temp_path,
                "temp_dir": temp_dir,
                "temp_dir_exists": os.path.exists(temp_dir),
                "temp_dir_writable": os.access(temp_dir, os.W_OK) if os.path.exists(temp_dir) else False
            })
            raise Exception("GLB export failed: file not found")
        
        if os.path.getsize(temp_path) == 0:
            log_event("error", "gltf_export_failed_empty", {
                "thread_id": thread_id,
                "temp_path": temp_path
            })
            raise Exception("GLB export failed: file is empty")

        with open(temp_path, "rb") as f:
            glb_data = f.read()
        try:
            os.remove(temp_path)
        except OSError:
            pass

        filename = f"scene-{thread_id}.glb"
        result = Response(
            content=glb_data,
            media_type="model/gltf-binary",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
        set_owner_headers(result, resolution)
        return result
    except Exception as e:
        import traceback
        traceback.print_exc()
        if isinstance(e, HTTPException):
            raise
        if isinstance(e, SessionResourceError):
            raise HTTPException(status_code=503, detail=e.detail) from e
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/scene/{thread_id}/objects/add-primitive", response_model=AddPrimitiveResponse)
async def add_primitive(
    thread_id: str,
    payload: AddPrimitiveRequest,
    request: Request,
    response: Response,
):
    resolution, proxied = await claim_or_proxy_request(request=request, thread_id=thread_id)
    if proxied is not None:
        return proxied

    try:
        settings = get_settings()
        if settings.blender_mode != "headless":
            raise HTTPException(
                status_code=409,
                detail="Add primitive is only available in headless mode.",
            )

        manager = get_session_manager()
        manager.ensure(thread_id, "headless")

        new_id = str(uuid.uuid4())
        code = _build_primitive_code(
            payload.primitive_type,
            payload.location,
            payload.size,
            new_id,
        )

        exec_result = await asyncio.to_thread(
            send_blender_command_sync,
            "execute_code",
            {"code": code},
            thread_id,
        )
        object_name, reported_object_id = _parse_created_primitive_marker((exec_result or {}).get("result"))
        if reported_object_id and reported_object_id != new_id:
            raise HTTPException(
                status_code=500,
                detail="Add primitive returned an unexpected backend object id.",
            )

        await asyncio.to_thread(ensure_scene_agent_object_metadata_sync, thread_id)

        manager.persist_session_blend(thread_id, min_interval_seconds=0.0)
        manifest = await asyncio.to_thread(persist_thread_scene_artifacts_sync, thread_id)
        if manifest is None:
            manifest = load_thread_scene_artifact_manifest(thread_id)

        resolved_name = object_name or _PRIMITIVE_DISPLAY_NAMES[payload.primitive_type]
        result = AddPrimitiveResponse(
            thread_id=thread_id,
            primitive_type=payload.primitive_type,
            object_name=resolved_name,
            backend_object_id=new_id,
            backend_object_name=resolved_name,
            scene_revision=manifest.scene_revision,
            manifest_generated_at_ms=manifest.generated_at_ms,
            has_persisted_blend=manifest.has_persisted_blend,
        )
        set_owner_headers(response, resolution)
        return result
    except Exception as e:
        import traceback
        traceback.print_exc()
        if isinstance(e, HTTPException):
            raise
        if isinstance(e, SessionResourceError):
            raise HTTPException(status_code=503, detail=e.detail) from e
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/scene/{thread_id}/objects/delete", response_model=DeleteSceneObjectResponse)
async def delete_scene_object(
    thread_id: str,
    payload: DeleteSceneObjectRequest,
    request: Request,
    response: Response,
):
    resolution, proxied = await claim_or_proxy_request(request=request, thread_id=thread_id)
    if proxied is not None:
        return proxied

    try:
        settings = get_settings()
        if settings.blender_mode != "headless":
            raise HTTPException(
                status_code=409,
                detail="Hierarchy delete is only available in headless mode.",
            )

        manager = get_session_manager()
        manager.ensure(thread_id, "headless")

        resolved = await asyncio.to_thread(
            send_blender_command_sync,
            "resolve_scene_agent_object",
            {
                "backend_object_id": payload.backend_object_id,
                "backend_object_name": payload.backend_object_name,
            },
            thread_id,
        )
        resolved_name = str((resolved or {}).get("object_name") or "").strip()
        if not resolved_name:
            raise HTTPException(
                status_code=404,
                detail=f"Scene object '{payload.backend_object_id}' was not found.",
            )

        delete_result = await asyncio.to_thread(
            send_blender_command_sync,
            "delete_objects",
            {
                "object_names": [resolved_name],
                "mode": payload.mode,
                "strict": True,
                "dry_run": False,
                "ignore_missing": False,
                "name_match_mode": "exact",
            },
            thread_id,
        )

        deleted_names_raw = (delete_result or {}).get("deleted")
        deleted_names = (
            [name for name in deleted_names_raw if isinstance(name, str) and name.strip()]
            if isinstance(deleted_names_raw, list)
            else []
        )

        manager.persist_session_blend(thread_id, min_interval_seconds=0.0)
        manifest = await asyncio.to_thread(persist_thread_scene_artifacts_sync, thread_id)
        if manifest is None:
            manifest = load_thread_scene_artifact_manifest(thread_id)

        result = DeleteSceneObjectResponse(
            thread_id=thread_id,
            backend_object_id=payload.backend_object_id,
            backend_object_name=str((resolved or {}).get("backend_object_name") or payload.backend_object_name or resolved_name),
            deleted_names=deleted_names,
            scene_revision=manifest.scene_revision,
            manifest_generated_at_ms=manifest.generated_at_ms,
            has_persisted_blend=manifest.has_persisted_blend,
        )
        set_owner_headers(response, resolution)
        return result
    except Exception as e:
        import traceback
        traceback.print_exc()
        if isinstance(e, HTTPException):
            raise
        if isinstance(e, SessionResourceError):
            raise HTTPException(status_code=503, detail=e.detail) from e
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/scene/{thread_id}/objects/transform", response_model=TransformSceneObjectResponse)
async def transform_scene_object(
    thread_id: str,
    payload: TransformSceneObjectRequest,
    request: Request,
    response: Response,
):
    resolution, proxied = await claim_or_proxy_request(request=request, thread_id=thread_id)
    if proxied is not None:
        return proxied

    try:
        settings = get_settings()
        if settings.blender_mode != "headless":
            raise HTTPException(
                status_code=409,
                detail="Object transform is only available in headless mode.",
            )

        manager = get_session_manager()
        manager.ensure(thread_id, "headless")

        resolved = await asyncio.to_thread(
            send_blender_command_sync,
            "resolve_scene_agent_object",
            {
                "backend_object_id": payload.backend_object_id,
                "backend_object_name": payload.backend_object_name,
            },
            thread_id,
        )
        resolved_name = str((resolved or {}).get("object_name") or "").strip()
        if not resolved_name:
            raise HTTPException(
                status_code=404,
                detail=f"Scene object '{payload.backend_object_id}' was not found.",
            )

        transform_result = await asyncio.to_thread(
            send_blender_command_sync,
            "set_object_transform",
            {
                "backend_object_id": payload.backend_object_id,
                "backend_object_name": payload.backend_object_name,
                "world_matrix": payload.world_matrix,
            },
            thread_id,
        )

        manifest = None
        if payload.refresh_artifacts:
            manager.persist_session_blend(thread_id, min_interval_seconds=0.0)
            manifest = await asyncio.to_thread(persist_thread_scene_artifacts_sync, thread_id)
            if manifest is None:
                manifest = load_thread_scene_artifact_manifest(thread_id)

        result = TransformSceneObjectResponse(
            thread_id=thread_id,
            backend_object_id=payload.backend_object_id,
            backend_object_name=str(
                (transform_result or {}).get("backend_object_name")
                or (resolved or {}).get("backend_object_name")
                or payload.backend_object_name
                or resolved_name
            ),
            object_name=str((transform_result or {}).get("object_name") or resolved_name),
            object_type=(transform_result or {}).get("object_type"),
            world_location=[
                float(value)
                for value in ((transform_result or {}).get("world_location") or [])
                if isinstance(value, (int, float))
            ],
            world_rotation_quaternion=[
                float(value)
                for value in ((transform_result or {}).get("world_rotation_quaternion") or [])
                if isinstance(value, (int, float))
            ],
            world_scale=[
                float(value)
                for value in ((transform_result or {}).get("world_scale") or [])
                if isinstance(value, (int, float))
            ],
            scene_revision=manifest.scene_revision if manifest else None,
            manifest_generated_at_ms=manifest.generated_at_ms if manifest else None,
            has_persisted_blend=manifest.has_persisted_blend if manifest else False,
            artifacts_refreshed=manifest is not None,
        )
        set_owner_headers(response, resolution)
        return result
    except Exception as e:
        import traceback
        traceback.print_exc()
        if isinstance(e, HTTPException):
            raise
        if isinstance(e, SessionResourceError):
            raise HTTPException(status_code=503, detail=e.detail) from e
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/scene/{thread_id}/blend")
async def get_scene_blend(thread_id: str, request: Request):
    """
    Export current Blender scene to .blend file and return the binary.
    """
    resolution, proxied = await claim_or_proxy_request(request=request, thread_id=thread_id)
    if proxied is not None:
        return proxied
    try:
        settings = get_settings()
        temp_path = os.path.join(
            tempfile.gettempdir(),
            f"scene_{thread_id}_{int(time.time() * 1000)}.blend"
        )
        export_code = (
            "import bpy\n"
            f"bpy.ops.wm.save_as_mainfile(filepath=r\"{temp_path}\", copy=True)\n"
        )
        if settings.blender_mode == "headless":
            await execute_headless_export_code(
                thread_id=thread_id,
                export_code=export_code,
                timeout_seconds=float(settings.headless_request_timeout_seconds),
                timeout_error_message="Headless BLEND export timed out.",
                timeout_event_name="headless_blend_timeout",
                failed_event_name="headless_blend_failed",
                ok_event_name="headless_blend_ok",
                restart_on_timeout=False,
            )
        else:
            await asyncio.to_thread(send_blender_command_sync, "execute_code", {"code": export_code}, thread_id)

        # Wait for file to be written with retries
        max_retries = 10
        retry_delay = 0.2
        for attempt in range(max_retries):
            if os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                break
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
        
        if not os.path.exists(temp_path):
            raise Exception("BLEND export failed: file not found")
        
        if os.path.getsize(temp_path) == 0:
            raise Exception("BLEND export failed: file is empty")

        with open(temp_path, "rb") as f:
            blend_data = f.read()
        try:
            os.remove(temp_path)
        except OSError:
            pass

        filename = f"scene-{thread_id}.blend"
        result = Response(
            content=blend_data,
            media_type="application/x-blender",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
        set_owner_headers(result, resolution)
        return result
    except Exception as e:
        import traceback
        traceback.print_exc()
        if isinstance(e, HTTPException):
            raise
        if isinstance(e, SessionResourceError):
            raise HTTPException(status_code=503, detail=e.detail) from e
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/scene/{thread_id}/blends", response_model=BlendFileListResponse)
async def list_scene_blends(thread_id: str, request: Request, response: Response):
    """
    List persisted .blend files available for this thread.
    """
    resolution, proxied = await claim_or_proxy_request(request=request, thread_id=thread_id)
    if proxied is not None:
        return proxied

    settings = get_settings()
    if settings.blender_mode == "headless":
        try:
            get_session_manager().persist_session_blend(
                thread_id,
                min_interval_seconds=8.0,
            )
        except Exception:
            pass

    storage_dir = resolve_thread_storage_dir(thread_id)
    files_with_mtime: list[tuple[float, BlendFileEntry]] = []
    if storage_dir.exists():
        root = storage_dir.resolve()
        for candidate in storage_dir.rglob("*.blend"):
            if not candidate.is_file():
                continue
            try:
                resolved = candidate.resolve()
                relative_path = resolved.relative_to(root).as_posix()
                stat = resolved.stat()
            except Exception:
                continue
            files_with_mtime.append(
                (
                    float(stat.st_mtime),
                    BlendFileEntry(
                        relative_path=relative_path,
                        filename=resolved.name,
                        size_bytes=int(stat.st_size),
                        modified_at=datetime.fromtimestamp(
                            stat.st_mtime,
                            tz=timezone.utc,
                        ).isoformat(),
                        category=blend_file_category(relative_path),
                    ),
                )
            )

    files_with_mtime.sort(key=lambda item: (-item[0], item[1].relative_path))
    payload = BlendFileListResponse(
        thread_id=thread_id,
        files=[entry for _, entry in files_with_mtime],
    )
    set_owner_headers(response, resolution)
    return payload

@router.get("/scene/{thread_id}/blends/download")
async def download_scene_blend_file(thread_id: str, path: str, request: Request):
    """
    Download one persisted .blend file for this thread.
    """
    resolution, proxied = await claim_or_proxy_request(request=request, thread_id=thread_id)
    if proxied is not None:
        return proxied

    normalized = path.strip().replace("\\", "/").lstrip("/")
    if not normalized or normalized.endswith("/") or "\x00" in normalized:
        raise HTTPException(status_code=400, detail="Invalid blend path.")

    path_parts = [part for part in normalized.split("/") if part]
    if not path_parts or any(part in {".", ".."} for part in path_parts):
        raise HTTPException(status_code=400, detail="Invalid blend path.")

    storage_dir = resolve_thread_storage_dir(thread_id)
    root = storage_dir.resolve()
    target = (root / "/".join(path_parts)).resolve()
    if target != root and root not in target.parents:
        raise HTTPException(status_code=400, detail="Invalid blend path.")
    if target.suffix.lower() != ".blend":
        raise HTTPException(status_code=400, detail="Only .blend files are supported.")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Blend file not found.")

    with open(target, "rb") as handle:
        blend_data = handle.read()

    result = Response(
        content=blend_data,
        media_type="application/x-blender",
        headers={"Content-Disposition": f'attachment; filename="{target.name}"'},
    )
    set_owner_headers(result, resolution)
    return result


@router.get(
    "/threads/{thread_id}/scene-artifacts/manifest",
    response_model=SceneArtifactManifestResponse,
)
async def get_thread_scene_artifact_manifest(thread_id: str):
    return load_thread_scene_artifact_manifest(thread_id)


@router.get("/threads/{thread_id}/scene-artifacts/latest.glb")
async def download_thread_scene_artifact_gltf(thread_id: str):
    gltf_path = resolve_thread_artifact_gltf_path(thread_id)
    if not gltf_path.exists() or not gltf_path.is_file():
        raise HTTPException(status_code=404, detail="Persisted GLB not found.")
    return FileResponse(
        path=str(gltf_path),
        media_type="model/gltf-binary",
        filename=f"scene-{thread_id}.glb",
    )


@router.get("/threads/{thread_id}/scene-artifacts/renders/{filename}")
async def get_thread_scene_artifact_render(thread_id: str, filename: str):
    normalized = filename.strip().replace("\\", "/").lstrip("/")
    if not normalized or "/" in normalized or "\x00" in normalized:
        raise HTTPException(status_code=400, detail="Invalid render filename.")
    renders_dir = resolve_thread_artifact_renders_dir(thread_id).resolve()
    target = (renders_dir / normalized).resolve()
    if target.parent != renders_dir:
        raise HTTPException(status_code=400, detail="Invalid render filename.")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Persisted render not found.")
    return FileResponse(
        path=str(target),
        media_type="image/jpeg",
        filename=target.name,
    )
