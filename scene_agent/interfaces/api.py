"""
FastAPI REST server for the 3D scene agent.
Provides HTTP endpoints and WebSocket support with streaming.
"""
import asyncio
import base64
import json
import os
import tempfile
import threading
import time
from typing import Dict, Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse, Response, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from langchain_core.messages import HumanMessage
from scene_agent.blender.connection import BlenderConnection

from scene_agent.agent.graph import create_agent_graph
from scene_agent.blender.session_manager import (
    allocate_headless_port,
    build_headless_command_args,
    get_session_manager,
    start_headless_process,
)
from scene_agent.config import get_settings
from scene_agent.memory.scene_memory import SceneMemory
from scene_agent.memory.reference_image_memory import (
    ReferenceImage,
    get_reference_image_memory,
)
from scene_agent.utils.diagnostics import (
    build_diagnostic_record,
    elapsed_ms,
    new_request_id,
    start_timer,
    within_target,
)
from scene_agent.utils.logging import log_event
from scene_agent.utils.rendering import RENDERS_DIR

# Create FastAPI app
app = FastAPI(
    title="3D Scene Agent API",
    description="LangGraph-based 3D scene manipulation agent with Blender integration",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Image storage configuration
# Mount static files for renders
app.mount("/renders", StaticFiles(directory=str(RENDERS_DIR)), name="renders")

# Global agent instance
_agent_graph = None
_agent_graphs_by_thread: Dict[str, Any] = {}

# Blender addon connection (direct socket)
_blender_connection = None
_blender_lock = threading.Lock()


def get_blender_connection() -> BlenderConnection:
    global _blender_connection
    if _blender_connection is not None:
        return _blender_connection
    host = os.getenv("BLENDER_HOST", "localhost")
    port = int(os.getenv("BLENDER_PORT", "9876"))
    _blender_connection = BlenderConnection(host=host, port=port)
    if not _blender_connection.connect():
        _blender_connection = None
        raise Exception("Could not connect to Blender addon.")
    return _blender_connection


def get_blender_connection_for_thread(thread_id: str) -> BlenderConnection:
    settings = get_settings()
    if settings.blender_mode == "local-client":
        return get_blender_connection()

    manager = get_session_manager()
    session = manager.ensure(thread_id, "headless")
    host = os.getenv("BLENDER_HEADLESS_HOST", settings.blender_host)
    base_port = int(os.getenv("BLENDER_HEADLESS_BASE_PORT", "9876"))
    port_range = int(os.getenv("BLENDER_HEADLESS_PORT_RANGE", "16"))
    if session.port is None:
        used_ports = {item.port for item in manager.list_sessions() if item.port}
        port = allocate_headless_port(
            thread_id,
            base_port,
            port_range,
            used_ports=used_ports,
        )
        manager.set_endpoint(thread_id, host, port)
    else:
        port = session.port

    command, args = build_headless_command_args(thread_id, host, port)
    print("Building headless command and args...")
    print(f"Command: {command}")
    print(f"Args: {args}")

    with session.lock:
        print("Lock acquired.")
        connection = session.connection
        if not isinstance(connection, BlenderConnection):
            print("Creating new connection for the thread...")
            connection = BlenderConnection(host=host, port=port)
            session.connection = connection
        if not connection.connect():
            print("Starting headless process...")
            start_headless_process(session, command, args)
            
            # 等待进程启动并监控
            deadline = time.time() + settings.blender_headless_startup_timeout
            last_check = time.time()
            connected = False
            
            while time.time() < deadline:
                # 定期检查进程状态
                if time.time() - last_check > 2.0:
                    if session.process:
                        if session.process.poll() is not None:
                            error_msg = f"Blender process exited with code {session.process.returncode}"
                            if session.log_path and os.path.exists(session.log_path):
                                with open(session.log_path, 'r') as f:
                                    log_content = f.read()
                                error_msg += f"\n\nProcess Log:\n{log_content[-2000:]}"
                            manager.set_error(thread_id, error_msg)
                            raise Exception(error_msg)
                        print(f"  Process still running (PID: {session.process.pid}), waiting for connection...")
                    last_check = time.time()
                
                if connection.connect():
                    print(f"Successfully connected to Blender on {host}:{port}")
                    connected = True
                    break
                time.sleep(0.5)
            
            if not connected:
                error_msg = f"Connection timeout after {settings.blender_headless_startup_timeout}s"
                if session.log_path and os.path.exists(session.log_path):
                    with open(session.log_path, 'r') as f:
                        log_content = f.read()
                    error_msg += f"\n\nProcess Log:\n{log_content[-2000:]}"
                manager.set_error(thread_id, error_msg)
                raise Exception(error_msg)
        else:
            print("Connection already established.")

        if not connection.sock:
            error_message = "Could not connect to headless Blender session."
            manager.set_error(thread_id, error_message)
            raise Exception(error_message)

        manager.set_ready(thread_id, connection)
        print(f"Session ready for thread: {thread_id}")

    return connection


def send_blender_command_sync(
    command_type: str,
    params: Dict[str, Any] | None = None,
    thread_id: str | None = None
) -> Dict[str, Any]:
    global _blender_connection
    settings = get_settings()
    if settings.blender_mode == "headless" and thread_id:
        # 移除外层锁，避免与 get_blender_connection_for_thread() 内部的锁嵌套导致死锁
        # get_blender_connection_for_thread() 内部已经有 session.lock 保护
        blender = get_blender_connection_for_thread(thread_id)
        return blender.send_command(command_type, params)

    with _blender_lock:
        try:
            blender = get_blender_connection()
            return blender.send_command(command_type, params)
        except Exception:
            if _blender_connection:
                _blender_connection.disconnect()
            _blender_connection = None
            blender = get_blender_connection()
            return blender.send_command(command_type, params)


def serialize_message(message: Any) -> Dict[str, Any]:
    if isinstance(message, tuple) and len(message) == 2:
        message = message[0]
    if isinstance(message, dict):
        return message
    if hasattr(message, "type") or hasattr(message, "content"):
        return {
            "type": getattr(message, "type", None),
            "content": getattr(message, "content", None),
            "additional_kwargs": getattr(message, "additional_kwargs", None),
            "response_metadata": getattr(message, "response_metadata", None),
            "name": getattr(message, "name", None),
            "id": getattr(message, "id", None),
        }
    return {"type": "unknown", "content": str(message)}


def serialize_event(event: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(event, dict):
        return {"event": event}
    payload: Dict[str, Any] = {}
    for key, value in event.items():
        if key == "messages" and isinstance(value, list):
            payload[key] = [serialize_message(msg) for msg in value]
        else:
            payload[key] = value
    return payload


def message_content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                # Check if it's an image_url object
                if "image_url" in item and isinstance(item["image_url"], dict):
                    url = item["image_url"].get("url", "")
                    # Convert image_url to markdown if it's not a data URL
                    if url and not url.startswith("data:"):
                        parts.append(f"![image]({url})")
                    # Skip data URLs to avoid including base64 in text
                    continue
                elif isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
                else:
                    # Don't serialize large base64 data
                    if "base64" not in str(item):
                        parts.append(json.dumps(item, ensure_ascii=False, default=str))
            else:
                parts.append(str(item))
        return "".join(parts)
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False, default=str)
    return str(content)


def normalize_stream_event(event: Any) -> tuple[str | None, Any]:
    if isinstance(event, tuple) and len(event) == 2:
        return event[0], event[1]
    return None, event


def message_has_tool_calls(serialized: Dict[str, Any]) -> bool:
    additional_kwargs = serialized.get("additional_kwargs")
    if isinstance(additional_kwargs, dict):
        tool_calls = additional_kwargs.get("tool_calls")
        return isinstance(tool_calls, list) and len(tool_calls) > 0
    return False


def message_is_tool(serialized: Dict[str, Any]) -> bool:
    return serialized.get("type") == "tool"


async def get_agent(thread_id: str | None = None):
    """Get or create the agent graph (singleton or per-thread in headless mode)."""
    global _agent_graph, _agent_graphs_by_thread
    settings = get_settings()
    if settings.blender_mode == "headless" and thread_id:
        if thread_id not in _agent_graphs_by_thread:
            _agent_graphs_by_thread[thread_id] = await create_agent_graph(session_id=thread_id)
        return _agent_graphs_by_thread[thread_id]
    if _agent_graph is None:
        _agent_graph = await create_agent_graph()
    return _agent_graph


# Request/Response models
class ChatRequest(BaseModel):
    message: str
    thread_id: str = "default"


class ChatResponse(BaseModel):
    response: str
    thread_id: str
    todos: list[Dict[str, Any]] = []


class ReferenceImageResponse(BaseModel):
    id: str
    thread_id: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    uploaded_at: str


class ReferenceImageListResponse(BaseModel):
    thread_id: str
    images: list[ReferenceImageResponse]


def serialize_reference_image(image: ReferenceImage) -> ReferenceImageResponse:
    return ReferenceImageResponse(
        id=image.id,
        thread_id=image.thread_id,
        filename=image.filename,
        content_type=image.content_type,
        size_bytes=image.size_bytes,
        sha256=image.sha256,
        uploaded_at=image.uploaded_at,
    )


def build_headless_diagnostics(
    *,
    session,
    request_id: str,
    elapsed_ms_value: int,
    status: str,
    target_ms: int,
) -> dict[str, Any]:
    record = build_diagnostic_record(
        request_id=request_id,
        thread_id=session.session_id,
        session_id=session.session_id,
        process_id=session.process.pid if session.process else None,
        log_path=session.log_path,
        elapsed_ms_value=elapsed_ms_value,
        status=status,
    )
    payload = record.__dict__.copy()
    payload["within_target"] = within_target(elapsed_ms_value, target_ms)
    return payload


@app.on_event("startup")
async def startup_event():
    """Initialize agent on startup"""
    try:
        settings = get_settings()
        if settings.blender_mode == "headless":
            log_event("info", "startup_skip_agent_init", {"mode": settings.blender_mode})
            return
        await get_agent()
        print("✓ Agent initialized successfully")
    except Exception as e:
        print(f"✗ Failed to initialize agent: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """Ensure headless processes are cleaned up on shutdown."""
    try:
        get_session_manager().shutdown_all()
    except Exception as e:
        log_event("error", "shutdown_cleanup_failed", {"error": str(e)})


@app.get("/")
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
            "reference_images": "GET/POST /threads/{thread_id}/reference-images",
            "todos": "GET /todos/{thread_id}",
            "threads": "GET /threads",
            "websocket": "WS /ws"
        }
    }


@app.get("/health")
async def healthcheck():
    """Healthcheck endpoint."""
    settings = get_settings()
    return {
        "status": "ok",
        "timestamp": time.time(),
        "blender_mode": settings.blender_mode,
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Chat with the agent (non-streaming).
    
    Args:
        request: ChatRequest with message and thread_id
        
    Returns:
        ChatResponse with agent's response and todos
    """
    try:
        agent = await get_agent(request.thread_id)
        config = {"configurable": {"thread_id": request.thread_id}}
        
        # Run agent
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content=request.message)], "thread_id": request.thread_id},
            config=config
        )
        
        # Extract response
        last_message = result["messages"][-1]
        response_text = last_message.content if hasattr(last_message, "content") else str(last_message)
        
        return ChatResponse(
            response=response_text,
            thread_id=request.thread_id,
            todos=result.get("todos", [])
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    Chat with the agent (streaming via Server-Sent Events).
    
    Args:
        request: ChatRequest with message and thread_id
        
    Returns:
        StreamingResponse with SSE events
    """
    async def event_generator():
        import time 
        settings = get_settings()
        timeout_seconds = max(1, settings.api_stream_timeout_seconds)
        keepalive_interval = min(15.0, max(5.0, timeout_seconds / 4))
        deadline = time.time() + timeout_seconds
        request_id = f"{request.thread_id}:{int(time.time() * 1000)}"
        saw_message_stream = False
        saw_new_message = False
        existing_message_ids: set[str] = set()
        last_assistant_text: str | None = None
        scene_has_change = False
        done_payload: dict[str, Any] | None = None
        try:
            agent = await get_agent(request.thread_id)
            config = {"configurable": {"thread_id": request.thread_id}}
            try:
                state = await agent.aget_state(config)
                state_messages = []
                if hasattr(state, "values") and isinstance(state.values, dict):
                    state_messages = state.values.get("messages", []) or []
                for message in state_messages:
                    serialized = serialize_message(message)
                    message_type = serialized.get("type")
                    if message_type in {"ai", "assistant"}:
                        message_id = serialized.get("id")
                        if isinstance(message_id, str) and message_id:
                            existing_message_ids.add(message_id)
                        content_text = message_content_to_text(serialized.get("content"))
                        if content_text:
                            last_assistant_text = content_text
            except Exception:
                pass

            stream = agent.astream(
                {"messages": [HumanMessage(content=request.message)], "thread_id": request.thread_id},
                config=config,
                stream_mode=["messages", "values"]
            )
            next_event_task: asyncio.Task | None = None
            while True:
                if time.time() >= deadline:
                    if next_event_task is not None:
                        next_event_task.cancel()
                    raise TimeoutError("Stream timed out")
                if next_event_task is None:
                    next_event_task = asyncio.create_task(stream.__anext__())
                done, _pending = await asyncio.wait({next_event_task}, timeout=keepalive_interval)
                if not done:
                    yield ": keepalive\n\n"
                    continue
                try:
                    event = next_event_task.result()
                except StopAsyncIteration:
                    break
                finally:
                    next_event_task = None

                mode, payload = normalize_stream_event(event)
                is_message_stream = mode == "messages" or hasattr(mode, "content") or hasattr(mode, "type")
                if is_message_stream:
                    saw_message_stream = True
                if isinstance(payload, dict) and "todos" in payload and payload["todos"]:
                    yield f"data: {json.dumps({'todos': payload['todos']}, default=str)}\n\n"

                messages = None
                if is_message_stream:
                    messages = payload if mode == "messages" else [mode]
                elif isinstance(payload, dict) and "messages" in payload:
                    if not saw_message_stream:
                        messages = payload["messages"]

                if messages:
                    if not isinstance(messages, list):
                        messages = [messages]
                    for message in messages:
                        serialized = serialize_message(message)
                        message_type = serialized.get("type")
                        if message_has_tool_calls(serialized) or message_is_tool(serialized):
                            scene_has_change = True
                        if message_type in {"human", "system"}:
                            continue
                        if message_type == "tool":
                            payload = {"messages": [serialized], "scene_has_change": scene_has_change}
                            yield f"data: {json.dumps(payload, default=str)}\n\n"
                            continue
                        message_id = serialized.get("id")
                        if isinstance(message_id, str) and message_id in existing_message_ids:
                            continue
                        delta = message_content_to_text(serialized.get("content"))
                        if not delta:
                            continue
                        if (
                            not message_id
                            and not saw_new_message
                            and last_assistant_text
                            and delta == last_assistant_text
                        ):
                            continue
                        saw_new_message = True
                        if is_message_stream:
                            event_payload = {"delta": delta, "message_id": message_id}
                            yield f"data: {json.dumps(event_payload, default=str)}\n\n"
                        else:
                            payload = {"messages": [serialized]}
                            yield f"data: {json.dumps(payload, default=str)}\n\n"

            done_payload = {"event": "done", "scene_has_change": scene_has_change}
        except Exception as e:
            log_event(
                "error",
                "stream_failed",
                {
                    "request_id": request_id,
                    "thread_id": request.thread_id,
                    "error": str(e),
                },
            )
            error_event = {"error": str(e)}
            yield f"data: {json.dumps(error_event)}\n\n"
            done_payload = {"event": "done", "scene_has_change": scene_has_change}
        finally:
            if done_payload is not None:
                yield f"data: {json.dumps(done_payload)}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/scene/{thread_id}")
async def get_scene(thread_id: str):
    """
    Get current scene state for a thread.
    
    Args:
        thread_id: Thread identifier
        
    Returns:
        Scene objects and metadata
    """
    try:
        settings = get_settings()
        if settings.blender_mode == "headless":
            manager = get_session_manager()
            session = manager.ensure(thread_id, "headless")
            request_id = new_request_id(thread_id)
            start_time = start_timer()
            try:
                scene_info = await asyncio.wait_for(
                    asyncio.to_thread(send_blender_command_sync, "get_scene_info", None, thread_id),
                    timeout=settings.headless_request_timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                elapsed_value = elapsed_ms(start_time)
                diagnostics = build_headless_diagnostics(
                    session=session,
                    request_id=request_id,
                    elapsed_ms_value=elapsed_value,
                    status="timeout",
                    target_ms=settings.headless_request_timeout_seconds * 1000,
                )
                log_event("error", "headless_scene_timeout", diagnostics)
                raise HTTPException(
                    status_code=504,
                    detail={"error": "Headless scene request timed out.", **diagnostics},
                ) from exc
            except Exception as exc:
                elapsed_value = elapsed_ms(start_time)
                diagnostics = build_headless_diagnostics(
                    session=session,
                    request_id=request_id,
                    elapsed_ms_value=elapsed_value,
                    status="error",
                    target_ms=settings.headless_request_timeout_seconds * 1000,
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
                    target_ms=settings.headless_request_timeout_seconds * 1000,
                )
                log_event("info", "headless_scene_ok", diagnostics)
        else:
            scene_info = await asyncio.to_thread(send_blender_command_sync, "get_scene_info", None, thread_id)
        scene_objects = SceneMemory.parse_scene_info(scene_info)
        objects = scene_info.get("objects", []) if isinstance(scene_info, dict) else []
        cameras = [obj.get("name") for obj in objects if obj.get("type") == "CAMERA" and obj.get("name")]
        return {
            "thread_id": thread_id,
            "scene_objects": scene_objects,
            "persistent_cameras": cameras,
            "iteration_count": 0
        }
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        settings = get_settings()
        if settings.blender_mode == "local-client":
            raise HTTPException(
                status_code=503,
                detail="Blender client not connected. Start the Blender addon or enable headless mode."
            ) from e
        raise


@app.get("/scene/{thread_id}/renders")
async def get_scene_renders(thread_id: str, mode: str = "rgb"):
    """
    Render all cameras in the current Blender scene and return base64 PNGs.
    """
    try:
        settings = get_settings()
        diagnostics = None
        if settings.blender_mode == "headless":
            manager = get_session_manager()
            session = manager.ensure(thread_id, "headless")
            request_id = new_request_id(thread_id)
            start_time = start_timer()
            try:
                scene_info = await asyncio.wait_for(
                    asyncio.to_thread(send_blender_command_sync, "get_scene_info", None, thread_id),
                    timeout=settings.headless_request_timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                elapsed_value = elapsed_ms(start_time)
                diagnostics = build_headless_diagnostics(
                    session=session,
                    request_id=request_id,
                    elapsed_ms_value=elapsed_value,
                    status="timeout",
                    target_ms=settings.headless_request_timeout_seconds * 1000,
                )
                log_event("error", "headless_renders_scene_timeout", diagnostics)
                raise HTTPException(
                    status_code=504,
                    detail={"error": "Headless render request timed out.", **diagnostics},
                ) from exc
            except Exception as exc:
                elapsed_value = elapsed_ms(start_time)
                diagnostics = build_headless_diagnostics(
                    session=session,
                    request_id=request_id,
                    elapsed_ms_value=elapsed_value,
                    status="error",
                    target_ms=settings.headless_request_timeout_seconds * 1000,
                )
                log_event("error", "headless_renders_scene_failed", {**diagnostics, "error": str(exc)})
                raise HTTPException(
                    status_code=500,
                    detail={"error": str(exc), **diagnostics},
                ) from exc
        else:
            scene_info = await asyncio.to_thread(send_blender_command_sync, "get_scene_info", None, thread_id)
        objects = scene_info.get("objects", [])
        cameras = [obj.get("name") for obj in objects if obj.get("type") == "CAMERA"]

        renders = []
        start_time = start_timer()
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
                remaining = settings.headless_request_timeout_seconds - (elapsed_ms(start_time) / 1000)
                if remaining <= 0:
                    elapsed_value = elapsed_ms(start_time)
                    diagnostics = build_headless_diagnostics(
                        session=session,
                        request_id=request_id,
                        elapsed_ms_value=elapsed_value,
                        status="timeout",
                        target_ms=settings.headless_request_timeout_seconds * 1000,
                    )
                    log_event("error", "headless_renders_timeout", diagnostics)
                    raise HTTPException(
                        status_code=504,
                        detail={"error": "Headless render request timed out.", **diagnostics},
                    )
                result = await asyncio.wait_for(render_call, timeout=remaining)
            else:
                result = await render_call
            filepath = result.get("filepath") or temp_path
            if not os.path.exists(filepath):
                continue
            with open(filepath, "rb") as f:
                image_b64 = base64.b64encode(f.read()).decode("ascii")
            try:
                os.remove(filepath)
            except OSError:
                pass
            renders.append({
                "camera_name": camera_name,
                "image_base64": image_b64
            })

        if settings.blender_mode == "headless":
            elapsed_value = elapsed_ms(start_time)
            diagnostics = build_headless_diagnostics(
                session=session,
                request_id=request_id,
                elapsed_ms_value=elapsed_value,
                status="ok",
                target_ms=settings.headless_request_timeout_seconds * 1000,
            )
            log_event("info", "headless_renders_ok", diagnostics)
        return {"thread_id": thread_id, "renders": renders, "diagnostics": diagnostics}
    except Exception as e:
        import traceback
        traceback.print_exc()
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/scene/{thread_id}/gltf")
async def get_scene_gltf(thread_id: str):
    """
    Export current Blender scene to GLB and return the binary.
    """
    try:
        settings = get_settings()
        temp_path = os.path.join(
            tempfile.gettempdir(),
            f"scene_{thread_id}_{int(time.time() * 1000)}.glb"
        )
        export_code = (
            "import bpy\n"
            f"bpy.ops.export_scene.gltf(filepath=r\"{temp_path}\", "
            "export_format='GLB', export_apply=True)\n"
        )
        if settings.blender_mode == "headless":
            manager = get_session_manager()
            session = manager.ensure(thread_id, "headless")
            request_id = new_request_id(thread_id)
            start_time = start_timer()
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(
                        send_blender_command_sync,
                        "execute_code",
                        {"code": export_code},
                        thread_id,
                    ),
                    timeout=settings.headless_request_timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                elapsed_value = elapsed_ms(start_time)
                diagnostics = build_headless_diagnostics(
                    session=session,
                    request_id=request_id,
                    elapsed_ms_value=elapsed_value,
                    status="timeout",
                    target_ms=settings.headless_request_timeout_seconds * 1000,
                )
                log_event("error", "headless_gltf_timeout", diagnostics)
                raise HTTPException(
                    status_code=504,
                    detail={"error": "Headless GLTF export timed out.", **diagnostics},
                ) from exc
            except Exception as exc:
                elapsed_value = elapsed_ms(start_time)
                diagnostics = build_headless_diagnostics(
                    session=session,
                    request_id=request_id,
                    elapsed_ms_value=elapsed_value,
                    status="error",
                    target_ms=settings.headless_request_timeout_seconds * 1000,
                )
                log_event("error", "headless_gltf_failed", {**diagnostics, "error": str(exc)})
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
                    target_ms=settings.headless_request_timeout_seconds * 1000,
                )
                log_event("info", "headless_gltf_ok", diagnostics)
        else:
            await asyncio.to_thread(send_blender_command_sync, "execute_code", {"code": export_code}, thread_id)

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
        return Response(
            content=glb_data,
            media_type="model/gltf-binary",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/scene/{thread_id}/blend")
async def get_scene_blend(thread_id: str):
    """
    Export current Blender scene to .blend file and return the binary.
    """
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
            manager = get_session_manager()
            session = manager.ensure(thread_id, "headless")
            request_id = new_request_id(thread_id)
            start_time = start_timer()
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(
                        send_blender_command_sync,
                        "execute_code",
                        {"code": export_code},
                        thread_id,
                    ),
                    timeout=settings.headless_request_timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                elapsed_value = elapsed_ms(start_time)
                diagnostics = build_headless_diagnostics(
                    session=session,
                    request_id=request_id,
                    elapsed_ms_value=elapsed_value,
                    status="timeout",
                    target_ms=settings.headless_request_timeout_seconds * 1000,
                )
                log_event("error", "headless_blend_timeout", diagnostics)
                raise HTTPException(
                    status_code=504,
                    detail={"error": "Headless BLEND export timed out.", **diagnostics},
                ) from exc
            except Exception as exc:
                elapsed_value = elapsed_ms(start_time)
                diagnostics = build_headless_diagnostics(
                    session=session,
                    request_id=request_id,
                    elapsed_ms_value=elapsed_value,
                    status="error",
                    target_ms=settings.headless_request_timeout_seconds * 1000,
                )
                log_event("error", "headless_blend_failed", {**diagnostics, "error": str(exc)})
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
                    target_ms=settings.headless_request_timeout_seconds * 1000,
                )
                log_event("info", "headless_blend_ok", diagnostics)
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
        return Response(
            content=blend_data,
            media_type="application/x-blender",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/threads/{thread_id}/reference-images", response_model=ReferenceImageListResponse)
async def upload_reference_images(thread_id: str, images: list[UploadFile] = File(...)):
    """
    Upload reference images for a thread.
    """
    settings = get_settings()
    if not images:
        raise HTTPException(status_code=400, detail="No images provided.")
    if len(images) > settings.reference_image_max_count:
        raise HTTPException(status_code=400, detail="Too many images uploaded.")

    uploads: list[tuple[str, str, bytes]] = []
    for image in images:
        payload = await image.read()
        uploads.append((image.filename or "reference.png", image.content_type or "image/unknown", payload))

    memory = get_reference_image_memory()
    try:
        stored = memory.add_images(thread_id=thread_id, uploads=uploads)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ReferenceImageListResponse(
        thread_id=thread_id,
        images=[serialize_reference_image(image) for image in stored],
    )


@app.get("/threads/{thread_id}/reference-images", response_model=ReferenceImageListResponse)
async def list_reference_images(thread_id: str):
    """
    List reference images for a thread.
    """
    memory = get_reference_image_memory()
    images = memory.list_images(thread_id)
    return ReferenceImageListResponse(
        thread_id=thread_id,
        images=[serialize_reference_image(image) for image in images],
    )


@app.get("/todos/{thread_id}")
async def get_todos(thread_id: str):
    """
    Get current todos for a thread.
    
    Args:
        thread_id: Thread identifier
        
    Returns:
        List of todos with their status
    """
    try:
        agent = await get_agent(thread_id)
        config = {"configurable": {"thread_id": thread_id}}
        
        state = await agent.aget_state(config)
        
        return {
            "thread_id": thread_id,
            "todos": state.values.get("todos", [])
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/threads")
async def list_threads():
    """
    List all active threads (sessions).
    
    Returns:
        List of thread IDs
    """
    # TODO: Implement thread listing from checkpointer
    return {
        "threads": [],
        "message": "Thread listing not yet implemented"
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time bidirectional communication.
    
    Args:
        websocket: WebSocket connection
    """
    await websocket.accept()
    thread_id = "websocket-session"
    
    try:
        agent = await get_agent(thread_id)
        config = {"configurable": {"thread_id": thread_id}}
        
        while True:
            # Receive message from client
            data = await websocket.receive_text()
            message_data = json.loads(data)
            
            if "message" in message_data:
                # Stream response back to client
                async for event in agent.astream(
                    {"messages": [HumanMessage(content=message_data["message"])], "thread_id": thread_id},
                    config=config,
                    stream_mode=["messages", "values"]
                ):
                    await websocket.send_json(serialize_event(event), default=str)
            
    except WebSocketDisconnect:
        print(f"WebSocket disconnected: {thread_id}")
    except Exception as e:
        await websocket.send_json({"error": str(e)})
        await websocket.close()


def run_api(host: str = "0.0.0.0", port: int = 8000, workers: int | None = None):
    """
    Run the FastAPI server.
    
    Args:
        host: Host to bind to
        port: Port to listen on
        workers: Number of worker processes
    """
    import uvicorn
    settings = get_settings()
    worker_count = workers if workers is not None else settings.api_workers
    worker_count = max(1, worker_count)
    uvicorn.run("scene_agent.interfaces.api:app", host=host, port=port, workers=worker_count)
