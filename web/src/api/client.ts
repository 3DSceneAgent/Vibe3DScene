import type {
  McpToolsInfo,
  ReferenceImage,
  RenderImage,
  SceneInfo,
  StreamEvent,
  TodoItem,
  VlmModelsInfo,
  VlmProviderOption,
  ThreadVlmSelection
} from './types'

type StreamChatArgs = {
  baseUrl: string
  message: string
  threadId: string
  enabledMcpTools?: string[]
  vlmProvider?: string
  vlmModel?: string
  onEvent: (event: StreamEvent) => void
  signal?: AbortSignal
}

function parseRenderImages(payload: unknown): RenderImage[] {
  if (!payload || typeof payload !== 'object') return []
  const renders = (payload as { renders?: unknown }).renders
  if (!Array.isArray(renders)) return []
  return renders.flatMap((entry) => {
    if (!entry || typeof entry !== 'object') return []
    const maybe = entry as { camera_name?: unknown; image_url?: unknown }
    if (typeof maybe.camera_name !== 'string' || typeof maybe.image_url !== 'string') {
      return []
    }
    return [{ camera_name: maybe.camera_name, image_url: maybe.image_url }]
  })
}

export async function streamChat({
  baseUrl,
  message,
  threadId,
  enabledMcpTools,
  vlmProvider,
  vlmModel,
  onEvent,
  signal
}: StreamChatArgs) {
  const payload: Record<string, unknown> = { message, thread_id: threadId }
  if (enabledMcpTools) {
    payload.enabled_mcp_tools = enabledMcpTools
  }
  if (vlmProvider) {
    payload.vlm_provider = vlmProvider
  }
  if (vlmModel) {
    payload.vlm_model = vlmModel
  }
  const response = await fetch(`${baseUrl}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
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
  const data = (await response.json()) as unknown
  return parseRenderImages(data)
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

export async function getMcpTools(
  baseUrl: string,
  threadId: string,
  signal?: AbortSignal
): Promise<McpToolsInfo> {
  const response = await fetch(`${baseUrl}/threads/${threadId}/mcp-tools`, { signal })
  if (!response.ok) {
    throw new Error(`Failed to load MCP tools (${response.status})`)
  }
  const data = (await response.json()) as Partial<McpToolsInfo>
  const tool_hints: Record<string, string> = {}
  if (data.tool_hints && typeof data.tool_hints === 'object' && !Array.isArray(data.tool_hints)) {
    Object.entries(data.tool_hints).forEach(([key, value]) => {
      if (typeof key !== 'string' || !key) return
      if (typeof value !== 'string') return
      const normalized = value.trim()
      if (!normalized) return
      tool_hints[key] = normalized
    })
  }

  return {
    thread_id: typeof data.thread_id === 'string' ? data.thread_id : threadId,
    loaded: Boolean(data.loaded),
    tool_count: typeof data.tool_count === 'number' ? data.tool_count : 0,
    tools: Array.isArray(data.tools) ? data.tools.filter((item): item is string => typeof item === 'string') : [],
    tool_hints,
    blender_mode: typeof data.blender_mode === 'string' ? data.blender_mode : 'unknown'
  }
}

export async function getVlmModels(
  baseUrl: string,
  threadId?: string,
  signal?: AbortSignal
): Promise<VlmModelsInfo> {
  const query = threadId ? `?thread_id=${encodeURIComponent(threadId)}` : ''
  const response = await fetch(`${baseUrl}/vlm/models${query}`, { signal })
  if (!response.ok) {
    throw new Error(`Failed to load VLM models (${response.status})`)
  }
  const data = (await response.json()) as Partial<VlmModelsInfo>
  const providers = Array.isArray(data.providers)
    ? data.providers
        .map((item): VlmProviderOption | null => {
          if (!item || typeof item !== 'object') return null
          const provider = typeof item.provider === 'string' ? item.provider : ''
          const display_name = typeof item.display_name === 'string' ? item.display_name : provider
          const default_model = typeof item.default_model === 'string' ? item.default_model : ''
          const models = Array.isArray(item.models)
            ? item.models.filter((model): model is string => typeof model === 'string')
            : []
          if (!provider || !default_model) return null
          return {
            provider,
            display_name,
            default_model,
            models,
            configured: Boolean(item.configured)
          }
        })
        .filter((item): item is VlmProviderOption => item !== null)
    : []

  const rawSelection = data.thread_selection
  let threadSelection: ThreadVlmSelection | undefined
  if (rawSelection && typeof rawSelection === 'object') {
    const selectionProvider = typeof rawSelection.provider === 'string' ? rawSelection.provider : ''
    const selectionModel = typeof rawSelection.model === 'string' ? rawSelection.model : ''
    const selectionThreadId = typeof rawSelection.thread_id === 'string' ? rawSelection.thread_id : threadId ?? ''
    if (selectionProvider && selectionModel && selectionThreadId) {
      threadSelection = {
        thread_id: selectionThreadId,
        provider: selectionProvider,
        model: selectionModel,
        locked: Boolean(rawSelection.locked)
      }
    }
  }

  const defaultProvider =
    typeof data.default_provider === 'string'
      ? data.default_provider
      : providers[0]?.provider ?? 'openai'
  const defaultModel =
    typeof data.default_model === 'string'
      ? data.default_model
      : providers.find((item) => item.provider === defaultProvider)?.default_model ??
        providers[0]?.default_model ??
        ''

  return {
    providers,
    default_provider: defaultProvider,
    default_model: defaultModel,
    thread_selection: threadSelection
  }
}
