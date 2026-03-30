"""API routes."""

import asyncio
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from scene_agent.config import get_settings
from scene_agent.session import get_session_coordinator

from .models import (
    ClearThreadsResponse,
    ThreadHistoryResponse,
    ThreadListResponse,
    HeadlessRuntimeThreadEntry,
    HeadlessSessionCapacityResponse,
    HeadlessSessionDebugEntry,
    HeadlessSessionDebugResponse,
    ReleaseRuntimeResponse,
    RenameThreadTitleRequest,
    RenameThreadTitleResponse,
)
from .shared import (
    build_thread_history_payload,
    list_accessible_thread_ids,
    build_thread_summaries,
    claim_or_proxy_request,
    collect_headless_runtime_debug_entries,
    collect_headless_runtime_entries,
    ensure_frontend_client_can_manage_thread,
    log_event,
    normalize_thread_title,
    release_thread_runtime as release_thread_runtime_impl,
    resolve_frontend_client_id,
    set_owner_headers,
    teardown_thread_session,
)
router = APIRouter()
@router.get("/threads", response_model=ThreadListResponse)
async def list_threads(request: Request):
    """
    List all active threads (sessions).
    
    Returns:
        List of thread IDs
    """
    coordinator = get_session_coordinator()
    client_id = resolve_frontend_client_id(request)
    summaries = build_thread_summaries(frontend_client_id=client_id)
    return ThreadListResponse(
        threads=[summary.thread_id for summary in summaries],
        summaries=summaries,
    )


@router.delete("/threads", response_model=ClearThreadsResponse)
async def delete_all_threads(request: Request):
    request_client_id = resolve_frontend_client_id(request)
    thread_ids = list_accessible_thread_ids(frontend_client_id=request_client_id)
    deleted_thread_ids: list[str] = []
    failed_thread_ids: list[str] = []

    for thread_id in thread_ids:
        try:
            result = await asyncio.to_thread(teardown_thread_session, thread_id)
            deleted_thread_ids.append(thread_id)
            log_event(
                "info",
                "thread_deleted",
                {"thread_id": thread_id, "cleaned": result.get("cleaned", [])},
            )
        except Exception as exc:
            failed_thread_ids.append(thread_id)
            log_event(
                "error",
                "thread_delete_failed",
                {"thread_id": thread_id, "error": str(exc), "bulk_delete": True},
            )

    return ClearThreadsResponse(
        deleted_thread_ids=deleted_thread_ids,
        failed_thread_ids=failed_thread_ids,
    )


@router.get("/threads/{thread_id}/history", response_model=ThreadHistoryResponse)
async def get_thread_history(thread_id: str, request: Request):
    request_client_id = resolve_frontend_client_id(request)
    ensure_frontend_client_can_manage_thread(thread_id, request_client_id)
    return build_thread_history_payload(thread_id)

@router.get("/headless/session-capacity", response_model=HeadlessSessionCapacityResponse)
async def get_headless_session_capacity(request: Request):
    settings = get_settings()
    client_id = resolve_frontend_client_id(request)
    quota = settings.resolve_frontend_session_quota(client_id)
    entries = collect_headless_runtime_entries(frontend_client_id=client_id)
    occupying_threads = [entry for entry in entries if entry.get("occupying_resources")]
    return HeadlessSessionCapacityResponse(
        blender_mode=settings.blender_mode,
        frontend_client_id=client_id,
        quota=quota,
        in_use=len(occupying_threads),
        occupying_threads=[HeadlessRuntimeThreadEntry(**entry) for entry in occupying_threads],
    )

@router.get("/headless/session-debug", response_model=HeadlessSessionDebugResponse)
async def get_headless_session_debug(
    request: Request,
    include_all_clients: bool = False,
):
    settings = get_settings()
    client_id = resolve_frontend_client_id(request)
    now_ms, entries = collect_headless_runtime_debug_entries(
        frontend_client_id=client_id,
        include_all_clients=include_all_clients,
    )
    occupying_sessions = sum(1 for entry in entries if bool(entry.get("occupying_resources")))
    return HeadlessSessionDebugResponse(
        blender_mode=settings.blender_mode,
        frontend_client_id=client_id,
        include_all_clients=bool(include_all_clients),
        now_ms=now_ms,
        session_idle_timeout_seconds=settings.session_idle_timeout_seconds,
        session_sweep_interval_seconds=settings.session_sweep_interval_seconds,
        total_sessions=len(entries),
        occupying_sessions=occupying_sessions,
        sessions=[HeadlessSessionDebugEntry(**entry) for entry in entries],
    )

@router.delete("/threads/{thread_id}")
async def delete_thread(thread_id: str, request: Request):
    """
    Delete a thread and tear down all associated resources.

    Kills headless Blender/MCP processes, releases ports, and cleans up
    Redis metadata and in-memory caches.
    """
    resolution, proxied = await claim_or_proxy_request(request=request, thread_id=thread_id)
    if proxied is not None:
        return proxied

    try:
        result = await asyncio.to_thread(teardown_thread_session, thread_id)
        log_event(
            "info",
            "thread_deleted",
            {"thread_id": thread_id, "cleaned": result.get("cleaned", [])},
        )
        return result
    except Exception as exc:
        log_event(
            "error",
            "thread_delete_failed",
            {"thread_id": thread_id, "error": str(exc)},
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc

@router.patch("/threads/{thread_id}/title", response_model=RenameThreadTitleResponse)
async def rename_thread_title(
    thread_id: str,
    payload: RenameThreadTitleRequest,
    request: Request,
    response: Response,
):
    resolution, proxied = await claim_or_proxy_request(request=request, thread_id=thread_id)
    if proxied is not None:
        return proxied

    title = normalize_thread_title(payload.title)
    if not title:
        raise HTTPException(status_code=422, detail="Thread title must not be empty.")

    try:
        request_client_id = resolve_frontend_client_id(request)
        ensure_frontend_client_can_manage_thread(thread_id, request_client_id)
        get_session_coordinator().update_session_runtime_fields(thread_id, {"title": title})
        set_owner_headers(response, resolution)
        return RenameThreadTitleResponse(thread_id=thread_id, title=title)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

@router.post("/threads/{thread_id}/release-runtime", response_model=ReleaseRuntimeResponse)
async def release_thread_runtime(thread_id: str, request: Request, response: Response):
    resolution, proxied = await claim_or_proxy_request(request=request, thread_id=thread_id)
    if proxied is not None:
        return proxied
    try:
        request_client_id = resolve_frontend_client_id(request)
        ensure_frontend_client_can_manage_thread(thread_id, request_client_id)
        result = await asyncio.to_thread(release_thread_runtime_impl, thread_id)
        set_owner_headers(response, resolution)
        return ReleaseRuntimeResponse(
            thread_id=thread_id,
            released=bool(result.get("released", False)),
            cleaned=[str(item) for item in result.get("cleaned", [])],
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
