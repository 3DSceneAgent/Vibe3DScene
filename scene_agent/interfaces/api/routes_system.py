"""API routes."""
from importlib import import_module
import time 
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from scene_agent.blender.session_manager import SessionResourceError
from scene_agent.config import get_settings
from scene_agent.session import get_session_coordinator

from .models import (
    ExamplePromptsResponse,
    MCPToolsResponse,
    ThreadVLMSelectionResponse,
    VLMModelsResponse,
    VLMProviderOption,
)
from .shared import (
    build_default_tool_hint,
    build_vlm_provider_catalog,
    claim_or_proxy_request,
    ensure_thread_vlm_config,
    extract_available_tool_hints,
    extract_available_tool_names,
    load_example_prompts,
    set_owner_headers,
)


def resolve_api_module():
    return import_module("scene_agent.interfaces.api")


async def get_agent(thread_id: str | None = None):
    api_module = resolve_api_module()
    return await api_module.get_agent(thread_id)


router = APIRouter()
@router.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "3D Scene Agent API",
        "status": "running",
        "endpoints": {
            "health": "GET /health",
            "chat": "POST /chat",
            "chat_stream": "POST /chat/stream",
            "scene": "GET /scene/{thread_id}",
            "scene_renders": "GET /scene/{thread_id}/renders",
            "scene_gltf": "GET /scene/{thread_id}/gltf",
            "images": "GET/POST /threads/{thread_id}/images",
            "example_prompts": "GET /example-prompts",
            "vlm_models": "GET /vlm/models",
            "mcp_tools": "GET /threads/{thread_id}/mcp-tools",
            "session_capacity": "GET /headless/session-capacity",
            "session_debug": "GET /headless/session-debug",
            "release_runtime": "POST /threads/{thread_id}/release-runtime",
            "rename_thread_title": "PATCH /threads/{thread_id}/title",
            "todos": "GET /todos/{thread_id}",
            "threads": "GET /threads",
            "delete_thread": "DELETE /threads/{thread_id}"
        }
    }

@router.get("/health")
async def healthcheck():
    """Healthcheck endpoint."""
    settings = get_settings()
    coordinator = get_session_coordinator()
    redis_ok, redis_latency_ms = coordinator.redis_health()
    return {
        "status": "ok",
        "timestamp": time.time(),
        "blender_mode": settings.blender_mode,
        "features": {"fast_mode": True},
        "defaults": {"fast_mode": settings.fast_mode_default},
        "worker_id": settings.api_worker_id,
        "redis_ok": redis_ok,
        "redis_latency_ms": redis_latency_ms,
        "session_idle_timeout_seconds": settings.session_idle_timeout_seconds,
        "session_sweep_interval_seconds": settings.session_sweep_interval_seconds,
    }

@router.get("/example-prompts", response_model=ExamplePromptsResponse)
async def get_example_prompts():
    try:
        prompts = load_example_prompts()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="assets/example_prompts.md not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return ExamplePromptsResponse(prompts=prompts)

@router.get("/vlm/models", response_model=VLMModelsResponse)
async def get_vlm_models(thread_id: str | None = None):
    settings = get_settings()
    providers = build_vlm_provider_catalog()
    thread_selection: ThreadVLMSelectionResponse | None = None
    if thread_id:
        state = ensure_thread_vlm_config(thread_id)
        thread_selection = ThreadVLMSelectionResponse(
            thread_id=thread_id,
            provider=state["provider"],
            model=state["model"],
            locked=False,
        )
    return VLMModelsResponse(
        providers=[VLMProviderOption(**provider) for provider in providers],
        default_provider=settings.vlm_provider,
        default_model=settings.get_vlm_default_model(settings.vlm_provider),
        thread_selection=thread_selection,
    )

@router.get("/threads/{thread_id}/mcp-tools", response_model=MCPToolsResponse)
async def get_mcp_tools(thread_id: str, request: Request, response: Response):
    settings = get_settings()
    resolution, proxied = await claim_or_proxy_request(request=request, thread_id=thread_id)
    if proxied is not None:
        return proxied
    try:
        agent = await get_agent(thread_id)
    except SessionResourceError as exc:
        raise HTTPException(status_code=503, detail=exc.detail) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Failed to load MCP tools for thread '{thread_id}': {exc}",
        ) from exc

    tools = extract_available_tool_names(agent)
    tool_hints_raw = extract_available_tool_hints(agent)
    tool_hints = {
        name: tool_hints_raw.get(name, build_default_tool_hint(name))
        for name in tools
    }
    payload = MCPToolsResponse(
        thread_id=thread_id,
        loaded=len(tools) > 0,
        tool_count=len(tools),
        tools=tools,
        tool_hints=tool_hints,
        blender_mode=settings.blender_mode,
    )
    set_owner_headers(response, resolution)
    return payload
