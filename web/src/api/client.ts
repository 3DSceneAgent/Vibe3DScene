import type { McpToolsInfo, ReferenceImage, RenderImage, SceneInfo, StreamEvent, TodoItem } from './types'

type StreamChatArgs = {
  baseUrl: string
  message: string
  threadId: string
  onEvent: (event: StreamEvent) => void
  signal?: AbortSignal
}

export async function streamChat({
  baseUrl,
  message,
  threadId,
  onEvent,
  signal
}: StreamChatArgs) {
  const response = await fetch(`${baseUrl}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, thread_id: threadId }),
    signal
  })

  if (!response.ok || !response.body) {
    throw new Error(`Stream failed (${response.status})`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let sawTerminalEvent = false

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const parts = buffer.split('\n\n')
    buffer = parts.pop() ?? ''

    for (const part of parts) {
      const lines = part.split('\n').filter((line) => line.startsWith('data:'))
      if (lines.length === 0) continue
      const data = lines.map((line) => line.replace(/^data:\s?/, '')).join('\n')
      if (!data) continue
      try {
        const parsed = JSON.parse(data) as StreamEvent
        if (parsed.event === 'done' || parsed.error) {
          sawTerminalEvent = true
        }
        onEvent(parsed)
      } catch (error) {
        console.error('Failed to parse stream event', error)
      }
    }
  }

  if (buffer.trim()) {
    const lines = buffer.split('\n').filter((line) => line.startsWith('data:'))
    if (lines.length > 0) {
      const data = lines.map((line) => line.replace(/^data:\s?/, '')).join('\n')
      if (data) {
        try {
          const parsed = JSON.parse(data) as StreamEvent
          if (parsed.event === 'done' || parsed.error) {
            sawTerminalEvent = true
          }
          onEvent(parsed)
        } catch (error) {
          console.error('Failed to parse stream event', error)
        }
      }
    }
  }

  if (!sawTerminalEvent && !signal?.aborted) {
    onEvent({ error: 'Stream closed unexpectedly.' })
  }
}

export async function getScene(
  baseUrl: string,
  threadId: string,
  signal?: AbortSignal
): Promise<SceneInfo> {
  const response = await fetch(`${baseUrl}/scene/${threadId}`, { signal })
  if (!response.ok) {
    throw new Error(`Failed to load scene (${response.status})`)
  }
  return (await response.json()) as SceneInfo
}

export async function getTodos(baseUrl: string, threadId: string): Promise<TodoItem[]> {
  const response = await fetch(`${baseUrl}/todos/${threadId}`)
  if (!response.ok) {
    throw new Error(`Failed to load todos (${response.status})`)
  }
  const data = (await response.json()) as { todos?: TodoItem[] }
  return data.todos ?? []
}

export async function getSceneRenders(
  baseUrl: string,
  threadId: string,
  signal?: AbortSignal
): Promise<RenderImage[]> {
  const response = await fetch(`${baseUrl}/scene/${threadId}/renders`, { signal })
  if (!response.ok) {
    throw new Error(`Failed to load renders (${response.status})`)
  }
  const data = (await response.json()) as { renders?: RenderImage[] }
  return data.renders ?? []
}

export async function getSceneGltf(
  baseUrl: string,
  threadId: string,
  signal?: AbortSignal
): Promise<Blob> {
  const response = await fetch(`${baseUrl}/scene/${threadId}/gltf`, { signal })
  if (!response.ok) {
    throw new Error(`Failed to load glTF (${response.status})`)
  }
  return await response.blob()
}

export async function getSceneBlend(
  baseUrl: string,
  threadId: string,
  signal?: AbortSignal
): Promise<Blob> {
  const response = await fetch(`${baseUrl}/scene/${threadId}/blend`, { signal })
  if (!response.ok) {
    throw new Error(`Failed to load .blend file (${response.status})`)
  }
  return await response.blob()
}

export async function uploadReferenceImages(
  baseUrl: string,
  threadId: string,
  files: File[]
): Promise<ReferenceImage[]> {
  const formData = new FormData()
  files.forEach((file) => formData.append('images', file))
  const response = await fetch(`${baseUrl}/threads/${threadId}/reference-images`, {
    method: 'POST',
    body: formData
  })
  if (!response.ok) {
    throw new Error(`Failed to upload reference images (${response.status})`)
  }
  const data = (await response.json()) as { images?: ReferenceImage[] }
  return data.images ?? []
}

export async function listReferenceImages(baseUrl: string, threadId: string): Promise<ReferenceImage[]> {
  const response = await fetch(`${baseUrl}/threads/${threadId}/reference-images`)
  if (!response.ok) {
    throw new Error(`Failed to load reference images (${response.status})`)
  }
  const data = (await response.json()) as { images?: ReferenceImage[] }
  return data.images ?? []
}

export async function getHealth(
  baseUrl: string,
  signal?: AbortSignal
): Promise<{ status: string; blender_mode?: 'headless' | 'local-client' }> {
  const response = await fetch(`${baseUrl}/health`, { signal })
  if (!response.ok) {
    throw new Error(`Healthcheck failed (${response.status})`)
  }
  return (await response.json()) as { status: string; blender_mode?: 'headless' | 'local-client' }
}


export async function getExamplePrompts(baseUrl: string): Promise<string[]> {
  const response = await fetch(`${baseUrl}/example-prompts`)
  if (!response.ok) {
    throw new Error(`Failed to load example prompts (${response.status})`)
  }
  const data = (await response.json()) as { prompts?: string[] }
  return Array.isArray(data.prompts) ? data.prompts : []
}

export async function getMcpTools(baseUrl: string, threadId: string): Promise<McpToolsInfo> {
  const response = await fetch(`${baseUrl}/threads/${threadId}/mcp-tools`)
  if (!response.ok) {
    throw new Error(`Failed to load MCP tools (${response.status})`)
  }
  const data = (await response.json()) as Partial<McpToolsInfo>
  return {
    thread_id: typeof data.thread_id === 'string' ? data.thread_id : threadId,
    loaded: Boolean(data.loaded),
    tool_count: typeof data.tool_count === 'number' ? data.tool_count : 0,
    tools: Array.isArray(data.tools) ? data.tools.filter((item): item is string => typeof item === 'string') : [],
    blender_mode: typeof data.blender_mode === 'string' ? data.blender_mode : 'unknown'
  }
}
