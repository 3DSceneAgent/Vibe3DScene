"""API routes."""

from importlib import import_module
from typing import Any
from fastapi import APIRouter, HTTPException, UploadFile, File, Request
from fastapi.responses import FileResponse, Response
from scene_agent.agent.redis_checkpointer import get_graph_checkpointer
from scene_agent.agent.todo_state import project_latest_todos
from scene_agent.blender.session_manager import SessionResourceError, get_session_manager
from scene_agent.config import get_settings
from scene_agent.memory.reference_image_memory import get_image_asset_memory
from scene_agent.session import get_session_coordinator

from .models import ImageAssetListResponse, serialize_image_asset
from .shared import (
    claim_or_proxy_request,
    claim_or_takeover_upload_request,
    normalize_image_limit,
    set_owner_headers,
)


def resolve_api_module():
    return import_module("scene_agent.interfaces.api")


async def get_agent(thread_id: str | None = None):
    api_module = resolve_api_module()
    return await api_module.get_agent(thread_id)


def _safe_int_optional(raw: object) -> int | None:
    try:
        if raw is None or raw == "":
            return None
        return int(raw)
    except (TypeError, ValueError):
        return None


def _thread_runtime_occupies_resources(thread_id: str) -> bool:
    session = get_session_manager().get(thread_id)
    if session is not None and session.mode == "headless":
        process_running = session.process is not None and session.process.poll() is None
        mcp_running = session.mcp_process is not None and session.mcp_process.poll() is None
        return bool(
            session.port is not None
            or session.mcp_port is not None
            or process_running
            or mcp_running
        )

    meta = get_session_coordinator().get_session_meta(thread_id) or {}
    return (
        _safe_int_optional(meta.get("blender_port")) is not None
        or _safe_int_optional(meta.get("mcp_port")) is not None
    )


def _load_persisted_thread_todos(thread_id: str) -> list[dict[str, Any]]:
    try:
        checkpointer = get_graph_checkpointer()
        get_tuple = getattr(checkpointer, "get_tuple", None)
        if not callable(get_tuple):
            return []
        snapshot = get_tuple({"configurable": {"thread_id": thread_id}})
    except Exception:
        return []

    checkpoint = getattr(snapshot, "checkpoint", None)
    if checkpoint is None and isinstance(snapshot, dict):
        checkpoint = snapshot.get("checkpoint")
    if not isinstance(checkpoint, dict):
        return []

    channel_values = checkpoint.get("channel_values")
    if not isinstance(channel_values, dict):
        return []

    return list(
        project_latest_todos(
            channel_values.get("todo_versions"),
            fallback_todos_raw=channel_values.get("todos"),
        )
    )


router = APIRouter()
@router.post("/threads/{thread_id}/images", response_model=ImageAssetListResponse)
async def upload_image_assets(
    thread_id: str,
    request: Request,
    response: Response,
    images: list[UploadFile] = File(...),
    source: str = "upload",
):
    """
    Upload image assets for a thread.
    """
    resolution = await claim_or_takeover_upload_request(request=request, thread_id=thread_id)
    settings = get_settings()
    if not images:
        raise HTTPException(status_code=400, detail="No images provided.")
    if len(images) > settings.reference_image_max_count:
        raise HTTPException(status_code=400, detail="Too many images uploaded.")

    uploads: list[tuple[str, str, bytes]] = []
    for image in images:
        payload = await image.read()
        uploads.append((image.filename or "image.png", image.content_type or "image/unknown", payload))

    memory = get_image_asset_memory()
    try:
        stored = memory.add_assets(
            thread_id=thread_id,
            uploads=uploads,
            source=source,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload = ImageAssetListResponse(
        thread_id=thread_id,
        images=[serialize_image_asset(image) for image in stored],
    )
    set_owner_headers(response, resolution)
    return payload

@router.get("/threads/{thread_id}/images", response_model=ImageAssetListResponse)
async def list_image_assets(
    thread_id: str,
    request: Request,
    response: Response,
    limit: int | None = None,
):
    """
    List image assets for a thread.
    """
    resolution, proxied = await claim_or_proxy_request(request=request, thread_id=thread_id)
    if proxied is not None:
        return proxied

    normalized_limit = normalize_image_limit(limit)

    memory = get_image_asset_memory()
    images = memory.list_assets(thread_id)
    if normalized_limit is not None and len(images) > normalized_limit:
        images = images[-normalized_limit:]

    payload = ImageAssetListResponse(
        thread_id=thread_id,
        images=[serialize_image_asset(image) for image in images],
    )
    set_owner_headers(response, resolution)
    return payload


@router.get("/threads/{thread_id}/images/{image_id}")
async def get_image_asset_file(thread_id: str, image_id: str):
    """
    Serve one persisted image asset file for stable message attachment rendering.
    """
    memory = get_image_asset_memory()
    assets = memory.get_assets_by_ids(thread_id, [image_id])
    if not assets:
        raise HTTPException(status_code=404, detail="Image asset not found.")
    asset = assets[0]
    if not asset.stored_path:
        raise HTTPException(status_code=404, detail="Image asset file is missing.")
    return FileResponse(
        path=asset.stored_path,
        media_type=asset.content_type or "application/octet-stream",
        filename=asset.filename or f"{asset.id}.png",
    )

@router.get("/todos/{thread_id}")
async def get_todos(thread_id: str, request: Request, response: Response):
    """
    Get current todos for a thread.
    
    Args:
        thread_id: Thread identifier
        
    Returns:
        List of todos with their status
    """
    resolution, proxied = await claim_or_proxy_request(request=request, thread_id=thread_id)
    if proxied is not None:
        return proxied
    settings = get_settings()
    if settings.blender_mode == "headless" and not _thread_runtime_occupies_resources(thread_id):
        payload = {
            "thread_id": thread_id,
            "todos": _load_persisted_thread_todos(thread_id),
        }
        set_owner_headers(response, resolution)
        return payload
    try:
        agent = await get_agent(thread_id)
        config = {"configurable": {"thread_id": thread_id}}
        
        state = await agent.aget_state(config)
        
        payload = {
            "thread_id": thread_id,
            "todos": state.values.get("todos", [])
        }
        set_owner_headers(response, resolution)
        return payload
        
    except Exception as e:
        if isinstance(e, SessionResourceError):
            raise HTTPException(status_code=503, detail=e.detail) from e
        raise HTTPException(status_code=500, detail=str(e))
