"""API routes."""

from importlib import import_module
from fastapi import APIRouter, HTTPException, UploadFile, File, Request
from fastapi.responses import Response
from scene_agent.blender.session_manager import SessionResourceError
from scene_agent.config import get_settings
from scene_agent.memory.reference_image_memory import get_image_asset_memory

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
