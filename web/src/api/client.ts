import type { RenderImage, SceneInfo, StreamEvent, TodoItem } from './types'

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
          onEvent(parsed)
        } catch (error) {
          console.error('Failed to parse stream event', error)
        }
      }
    }
  }
}

export async function getScene(baseUrl: string, threadId: string): Promise<SceneInfo> {
  const response = await fetch(`${baseUrl}/scene/${threadId}`)
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

export async function getSceneRenders(baseUrl: string, threadId: string): Promise<RenderImage[]> {
  const response = await fetch(`${baseUrl}/scene/${threadId}/renders`)
  if (!response.ok) {
    throw new Error(`Failed to load renders (${response.status})`)
  }
  const data = (await response.json()) as { renders?: RenderImage[] }
  return data.renders ?? []
}

export async function getSceneGltf(baseUrl: string, threadId: string): Promise<Blob> {
  const response = await fetch(`${baseUrl}/scene/${threadId}/gltf`)
  if (!response.ok) {
    throw new Error(`Failed to load glTF (${response.status})`)
  }
  return await response.blob()
}

export async function getHealth(baseUrl: string, signal?: AbortSignal): Promise<{ status: string }> {
  const response = await fetch(`${baseUrl}/health`, { signal })
  if (!response.ok) {
    throw new Error(`Healthcheck failed (${response.status})`)
  }
  return (await response.json()) as { status: string }
}
