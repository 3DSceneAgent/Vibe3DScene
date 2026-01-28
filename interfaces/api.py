"""
FastAPI REST server for the 3D scene agent.
Provides HTTP endpoints and WebSocket support with streaming.
"""
import asyncio
from typing import Dict, Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from langchain_core.messages import HumanMessage
import json

from agent.graph import create_agent_graph

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


async def get_agent():
    """Get or create the agent graph (singleton)"""
    global _agent_graph
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
        agent = await get_agent()
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
        try:
            agent = await get_agent()
            config = {"configurable": {"thread_id": request.thread_id}}
            
            async for event in agent.astream(
                {"messages": [HumanMessage(content=request.message)]},
                config=config,
                stream_mode="values"
            ):
                # Send event as JSON
                yield f"data: {json.dumps(event, default=str)}\n\n"
                
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
        agent = await get_agent()
        config = {"configurable": {"thread_id": thread_id}}
        
        state = await agent.aget_state(config)
        
        return {
            "thread_id": thread_id,
            "scene_objects": state.values.get("scene_objects", {}),
            "persistent_cameras": state.values.get("persistent_cameras", []),
            "iteration_count": state.values.get("iteration_count", 0)
        }
        
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
        agent = await get_agent()
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
        agent = await get_agent()
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
                    stream_mode="values"
                ):
                    await websocket.send_json(event, default=str)
            
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
