# 3D Scene Agent

LangGraph-based agent for 3D scene manipulation in Blender using natural language.

## Features

- 🤖 **VLM-Powered**: Uses GPT-4o, Claude, or Gemini for scene understanding
- 🔧 **MCP Integration**: Native integration with Blender via Model Context Protocol
- 📋 **Task Tracking**: Automatic todo creation and progress tracking for complex tasks
- 🎨 **Asset Libraries**: Search and import from PolyHaven, 3D retrieval database, or generate with TRELLIS2
- 💾 **State Persistence**: Checkpointing for session resumption
- 🔄 **Streaming**: Real-time responses in both CLI and API modes

## Quick Start

### 1. Setup Environment

```bash
# Copy environment template
cp .env.example .env

# Edit .env and add your API key
# VLM_PROVIDER=openai
# VLM_API_KEY=your_key_here
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Start Blender MCP Server

Make sure your Blender MCP server is running on `localhost:9876` (see `reference/server.py`)

### 4. Run the Agent

**CLI Mode:**
```bash
python main.py --mode cli
```

**API Mode:**
```bash
python main.py --mode api --port 8000
```

## Usage Examples

### CLI Interface

```
You: Create a modern living room with a sofa and coffee table

Agent: I'll break this down into steps:
📋 Task Progress:
⏳ Pending - Search for sofa model
⏳ Pending - Import and position sofa
⏳ Pending - Search for coffee table
⏳ Pending - Position table in front of sofa
⏳ Pending - Set up lighting

[Agent proceeds to execute each step...]
```

### API Endpoints

- `POST /chat` - Send a message and get response
- `POST /chat/stream` - Streaming response via SSE
- `GET /scene/{thread_id}` - Get current scene state
- `GET /todos/{thread_id}` - Get task progress
- `WS /ws` - WebSocket for real-time communication

## Architecture

### Key Components

- **State Management**: TypedDict with Annotated reducers (following LangGraph best practices)
- **Agent Graph**: Standard agent-tools loop where agent decides when to perceive/render
- **MCP Tools**: Auto-discovered from Blender server via `langchain-mcp-adapters`
- **Memory**: Scene object tracking and camera rendering history
- **RAG**: Placeholder for BPY script retrieval (to be implemented)

### Project Structure

```
3DSceneAgent/
├── agent/           # LangGraph state machine
├── tools/           # Blender MCP tool integration
├── memory/          # Scene and camera tracking
├── rag/             # BPY script retrieval (placeholder)
├── vlm/             # VLM provider abstraction
├── interfaces/      # CLI and API
├── config.py        # Configuration management
└── main.py          # Entry point
```

## Configuration

### Environment Variables

- `VLM_PROVIDER`: `openai`, `anthropic`, or `gemini` (default: openai)
- `VLM_API_KEY`: API key for the VLM provider (required)
- `VLM_MODEL`: Optional model override
- `BLENDER_HOST`: Blender MCP server host (default: localhost)
- `BLENDER_PORT`: Blender MCP server port (default: 9876)
- `RETRIEVAL_API_HOST`: 3D asset retrieval host (default: localhost)
- `RETRIEVAL_API_PORT`: 3D asset retrieval port (default: 8001)
- `RAG_ENABLED`: Enable RAG for BPY scripts (default: false)

## Development

### Todo Tracking

The agent automatically creates and tracks todos for complex tasks. See `agent/state.py` for the TodoItem schema.

### Adding BPY Documentation

To enable RAG:
1. Add BPY docs to a `docs/` directory
2. Implement embedding and ingestion in `rag/vector_store.py`
3. Set `RAG_ENABLED=true` in `.env`

### Testing

```bash
# Unit tests
pytest tests/

# Integration tests (requires Blender running)
pytest tests/integration/
```

## Troubleshooting

**"Failed to connect to Blender MCP server"**
- Make sure Blender MCP server is running on port 9876
- Check BLENDER_HOST and BLENDER_PORT in .env

**"VLM API key not set"**
- Create a .env file with VLM_API_KEY
- See .env.example for reference

**"langchain-mcp-adapters not installed"**
- Run: `pip install -r requirements.txt`

## References

- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)
- [langchain-mcp-adapters](https://github.com/langchain-ai/langchain-mcp-adapters)
- [Blender Python API](https://docs.blender.org/api/current/)

## License

MIT
