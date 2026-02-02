"""
FastAPI REST server for the 3D scene agent.
Provides HTTP endpoints and WebSocket support with streaming.
"""
import asyncio
import base64
import json
import os
import socket
import tempfile
import threading
import time
from typing import Dict, Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from langchain_core.messages import HumanMessage

from agent.graph import create_agent_graph
from blender.session_manager import (
    allocate_headless_port,
    build_headless_command_args,
    get_session_manager,
    start_headless_process,
)
from config import get_settings
from memory.scene_memory import SceneMemory

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

# Global agent instance
_agent_graph = None
_agent_graphs_by_thread: Dict[str, Any] = {}

# Blender addon connection (direct socket)
_blender_connection = None
_blender_lock = threading.Lock()


class BlenderConnection:
    """Minimal socket client for Blender addon commands."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.sock = None

    def connect(self) -> bool:
        if self.sock:
            return True
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            return True
        except Exception:
            self.sock = None
            return False

    def disconnect(self):
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def receive_full_response(self, buffer_size: int = 8192) -> bytes:
        chunks = []
        self.sock.settimeout(180.0)
        while True:
            chunk = self.sock.recv(buffer_size)
            if not chunk:
                break
            chunks.append(chunk)
            try:
                data = b"".join(chunks)
                json.loads(data.decode("utf-8"))
                return data
            except json.JSONDecodeError:
                continue
        if not chunks:
            raise Exception("No data received")
        data = b"".join(chunks)
        json.loads(data.decode("utf-8"))
        return data

    def send_command(self, command_type: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
        if not self.sock and not self.connect():
            raise ConnectionError("Not connected to Blender")
        command = {"type": command_type, "params": params or {}}
        self.sock.sendall(json.dumps(command).encode("utf-8"))
        response_data = self.receive_full_response()
        response = json.loads(response_data.decode("utf-8"))
        if response.get("status") == "error":
            raise Exception(response.get("message", "Unknown error from Blender"))
        return response.get("result", {})


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
    port_range = int(os.getenv("BLENDER_HEADLESS_PORT_RANGE", "1"))
    port = allocate_headless_port(thread_id, base_port, port_range)
    manager.set_endpoint(thread_id, host, port)

    command, args = build_headless_command_args(thread_id, host, port)

    with session.lock:
        connection = session.connection
        if not isinstance(connection, BlenderConnection):
            connection = BlenderConnection(host=host, port=port)
            session.connection = connection
        if not connection.connect():
            start_headless_process(session, command, args)
            deadline = time.time() + settings.blender_headless_startup_timeout
            while time.time() < deadline:
                if connection.connect():
                    break
                time.sleep(0.5)

        if not connection.sock:
            error_message = "Could not connect to headless Blender session."
            manager.set_error(thread_id, error_message)
            raise Exception(error_message)

        manager.set_ready(thread_id, connection)

    return connection


def send_blender_command_sync(
    command_type: str,
    params: Dict[str, Any] | None = None,
    thread_id: str | None = None
) -> Dict[str, Any]:
    global _blender_connection
    settings = get_settings()
    if settings.blender_mode == "headless" and thread_id:
        manager = get_session_manager()
        session = manager.ensure(thread_id, "headless")
        with session.lock:
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
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
                else:
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


@app.on_event("startup")
async def startup_event():
    """Initialize agent on startup"""
    try:
        await get_agent()
        print("✓ Agent initialized successfully")
    except Exception as e:
        print(f"✗ Failed to initialize agent: {e}")


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "3D Scene Agent API",
        "status": "running",
        "endpoints": {
            "chat": "POST /chat",
            "chat_stream": "POST /chat/stream",
            "scene": "GET /scene/{thread_id}",
            "scene_renders": "GET /scene/{thread_id}/renders",
            "scene_gltf": "GET /scene/{thread_id}/gltf",
            "todos": "GET /todos/{thread_id}",
            "threads": "GET /threads",
            "websocket": "WS /ws"
        }
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
            {"messages": [HumanMessage(content=request.message)]},
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
        saw_message_stream = False
        try:
            agent = await get_agent(request.thread_id)
            config = {"configurable": {"thread_id": request.thread_id}}

            async for event in agent.astream(
                {"messages": [HumanMessage(content=request.message)]},
                config=config,
                stream_mode=["messages", "values"]
            ):
                mode, payload = normalize_stream_event(event)
                if mode == "messages":
                    saw_message_stream = True
                if isinstance(payload, dict) and "todos" in payload and payload["todos"]:
                    yield f"data: {json.dumps({'todos': payload['todos']}, default=str)}\n\n"

                messages = None
                if isinstance(payload, dict) and "messages" in payload:
                    if not saw_message_stream:
                        messages = payload["messages"]
                elif mode == "messages":
                    messages = payload

                if messages:
                    if not isinstance(messages, list):
                        messages = [messages]
                    for message in messages:
                        serialized = serialize_message(message)
                        message_type = serialized.get("type")
                        if message_type in {"human", "tool", "system"}:
                            continue
                        if mode == "messages":
                            delta = message_content_to_text(serialized.get("content"))
                            if not delta:
                                continue
                            event_payload = {"delta": delta, "message_id": serialized.get("id")}
                            yield f"data: {json.dumps(event_payload, default=str)}\n\n"
                        else:
                            payload = {"messages": [serialized]}
                            yield f"data: {json.dumps(payload, default=str)}\n\n"

            yield f"data: {json.dumps({'event': 'done'})}\n\n"
        except Exception as e:
            error_event = {"error": str(e)}
            yield f"data: {json.dumps(error_event)}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream"
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
        settings = get_settings()
        if settings.blender_mode == "local-client":
            raise HTTPException(
                status_code=503,
                detail="Blender client not connected. Start the Blender addon or enable headless mode."
            ) from e
        try:
            agent = await get_agent(thread_id)
            config = {"configurable": {"thread_id": thread_id}}
            state = await agent.aget_state(config)
            return {
                "thread_id": thread_id,
                "scene_objects": state.values.get("scene_objects", {}),
                "persistent_cameras": state.values.get("persistent_cameras", []),
                "iteration_count": state.values.get("iteration_count", 0)
            }
        except Exception as fallback_error:
            raise HTTPException(status_code=500, detail=str(fallback_error)) from fallback_error


@app.get("/scene/{thread_id}/renders")
async def get_scene_renders(thread_id: str, mode: str = "rgb"):
    """
    Render all cameras in the current Blender scene and return base64 PNGs.
    """
    try:
        scene_info = await asyncio.to_thread(send_blender_command_sync, "get_scene_info", None, thread_id)
        objects = scene_info.get("objects", [])
        cameras = [obj.get("name") for obj in objects if obj.get("type") == "CAMERA"]

        renders = []
        for camera_name in cameras:
            if not camera_name:
                continue
            temp_path = os.path.join(
                tempfile.gettempdir(),
                f"blender_render_{camera_name}_{int(time.time() * 1000)}.png"
            )
            result = await asyncio.to_thread(
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

        return {"thread_id": thread_id, "renders": renders}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/scene/{thread_id}/gltf")
async def get_scene_gltf(thread_id: str):
    """
    Export current Blender scene to GLB and return the binary.
    """
    try:
        temp_path = os.path.join(
            tempfile.gettempdir(),
            f"scene_{thread_id}_{int(time.time() * 1000)}.glb"
        )
        export_code = (
            "import bpy\n"
            f"bpy.ops.export_scene.gltf(filepath=r\"{temp_path}\", "
            "export_format='GLB', export_apply=True)\n"
        )
        await asyncio.to_thread(send_blender_command_sync, "execute_code", {"code": export_code}, thread_id)

        if not os.path.exists(temp_path):
            raise Exception("GLB export failed")

        with open(temp_path, "rb") as f:
            glb_data = f.read()
        try:
            os.remove(temp_path)
        except OSError:
            pass

        return Response(content=glb_data, media_type="model/gltf-binary")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
                    {"messages": [HumanMessage(content=message_data["message"])]},
                    config=config,
                    stream_mode=["messages", "values"]
                ):
                    await websocket.send_json(serialize_event(event), default=str)
            
    except WebSocketDisconnect:
        print(f"WebSocket disconnected: {thread_id}")
    except Exception as e:
        await websocket.send_json({"error": str(e)})
        await websocket.close()


def run_api(host: str = "0.0.0.0", port: int = 8000):
    """
    Run the FastAPI server.
    
    Args:
        host: Host to bind to
        port: Port to listen on
    """
    import uvicorn
    uvicorn.run(app, host=host, port=port)
