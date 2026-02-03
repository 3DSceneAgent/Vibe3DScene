# Streaming Response Structure

This document describes how `/chat/stream` SSE payloads are shaped and how text, tool output, reasoning, and images appear in the stream.

## Transport

- Endpoint: `POST /chat/stream`
- Protocol: Server-Sent Events (SSE)
- Each SSE `data:` line contains a JSON object (a `StreamEvent`).

## StreamEvent Shapes

### 1) Incremental assistant text (`delta`)

Sent during the agent reply as partial chunks.

```json
{ "delta": "Hello ", "message_id": "msg-123" }
```

### 2) Tool/action messages (`messages`)

Tool outputs are emitted as structured messages. These may include render output or other payloads.

```json
{
  "messages": [
    {
      "type": "tool",
      "name": "blender.render_from_camera",
      "content": { "camera_name": "Camera", "image_base64": "..." },
      "additional_kwargs": null,
      "response_metadata": null,
      "id": "tool-123"
    }
  ],
  "scene_has_change": true
}
```

### 3) Todo updates

```json
{ "todos": [{ "id": "todo-1", "description": "Place key light", "status": "in_progress" }] }
```

### 4) Completion (`event: done`)

Emitted once per request when the stream completes.

```json
{ "event": "done", "scene_has_change": true }
```

## Message Envelope Fields

Each entry inside `messages` is serialized with:

- `type`: `ai`/`assistant`/`tool`/`human`/`system`
- `content`: string or structured content (list/object)
- `additional_kwargs`: includes tool call metadata (for assistant messages)
- `response_metadata`: provider-specific metadata
- `name`: tool name (for tool messages)
- `id`: message identifier

## Reasoning / Thinking

Some assistant messages include reasoning inside `<thinking>...</thinking>` tags. The UI should:

- Extract and store the `thinking` block separately
- Render `thinking` in a collapsible section
- Render the remaining text as normal assistant content

## Multimodal Content

`content` can be:

- A string
- A list of parts (strings or objects with `text`/`content`)
- A structured object (tool payloads)

For tool outputs, images often appear as:

- `image_base64` fields (base64-encoded PNG)
- URLs pointing to images (http/https)

The UI should scan tool payloads for image fields and render them inline.

## Ordering Guarantees

- SSE events arrive in the order produced by the LangGraph stream.
- Tool messages may interleave with assistant text.
- The `event: done` payload marks end of stream for a request.
