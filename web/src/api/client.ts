import type {
  BlendFileEntry,
  HeadlessSessionCapacityInfo,
  McpToolsInfo,
  ReferenceImage,
  ReleaseRuntimeInfo,
  RenderImage,
  SceneInfo,
  StreamEvent,
  TodoItem,
  VlmModelsInfo,
  VlmProviderOption,
  ThreadVlmSelection
} from './types'

const FRONTEND_CLIENT_HEADER = 'X-Frontend-Client-Id'
const FRONTEND_CLIENT_STORAGE_KEY = 'sceneAgentFrontendClientId'
let frontendClientIdCache: string | null = null

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function generateFrontendClientId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `web-${crypto.randomUUID()}`
  }
  const randomPart = Math.random().toString(36).slice(2, 10)
  return `web-${Date.now().toString(36)}-${randomPart}`
}

function getFrontendClientId(): string {
  if (frontendClientIdCache) {
    return frontendClientIdCache
  }
  try {
    const fromStorage = localStorage.getItem(FRONTEND_CLIENT_STORAGE_KEY)
    if (typeof fromStorage === 'string' && fromStorage.trim()) {
      frontendClientIdCache = fromStorage.trim()
      return frontendClientIdCache
    }
  } catch {
    // Ignore localStorage access failures (private mode / SSR).
  }
  const generated = generateFrontendClientId()
  frontendClientIdCache = generated
  try {
    localStorage.setItem(FRONTEND_CLIENT_STORAGE_KEY, generated)
  } catch {
    // Ignore persistence failures; in-memory ID still keeps consistency per tab.
  }
  return generated
}

function buildRequestHeaders(headers?: HeadersInit): Headers {
  const merged = new Headers(headers ?? undefined)
  merged.set(FRONTEND_CLIENT_HEADER, getFrontendClientId())
  return merged
}

async function apiFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const requestInit: RequestInit = {
    ...(init ?? {}),
    headers: buildRequestHeaders(init?.headers)
  }
  return await fetch(input, requestInit)
}

function extractDetailMessage(detail: unknown): string | undefined {
  if (typeof detail === 'string' && detail.trim()) {
    return detail.trim()
  }
  if (isRecord(detail)) {
    const directError = detail.error
    if (typeof directError === 'string' && directError.trim()) {
      return directError.trim()
    }
    const nestedDetail = detail.detail
    if (typeof nestedDetail === 'string' && nestedDetail.trim()) {
      return nestedDetail.trim()
    }
  }
  return undefined
}

export class ApiRequestError extends Error {
  status: number
  detail?: unknown
  reason?: string
  limits?: Record<string, unknown>
  in_use?: Record<string, unknown>

  constructor(args: {
    message: string
    status: number
    detail?: unknown
    reason?: string
    limits?: Record<string, unknown>
    in_use?: Record<string, unknown>
  }) {
    super(args.message)
    this.name = 'ApiRequestError'
    this.status = args.status
    this.detail = args.detail
    this.reason = args.reason
    this.limits = args.limits
    this.in_use = args.in_use
  }
}

async function buildHttpError(response: Response, fallbackMessage: string): Promise<ApiRequestError> {
  let detailPayload: unknown
  try {
    detailPayload = await response.json()
  } catch {
    detailPayload = undefined
  }

  const responseRoot = isRecord(detailPayload) ? detailPayload : {}
  const detail = responseRoot.detail
  const structuredDetail = isRecord(detail) ? detail : undefined
  const reasonValue = structuredDetail?.reason
  const reason = typeof reasonValue === 'string' ? reasonValue : undefined
  const limits = isRecord(structuredDetail?.limits) ? (structuredDetail.limits as Record<string, unknown>) : undefined
  const inUse = isRecord(structuredDetail?.in_use) ? (structuredDetail.in_use as Record<string, unknown>) : undefined
  const preferredMessage =
    extractDetailMessage(detail) ||
    extractDetailMessage(detailPayload) ||
    extractDetailMessage(responseRoot.error) ||
    fallbackMessage

  return new ApiRequestError({
    message: preferredMessage,
    status: response.status,
    detail: detail ?? detailPayload,
    reason,
    limits,
    in_use: inUse
  })
}

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

function parseBlendFiles(payload: unknown): BlendFileEntry[] {
  if (!payload || typeof payload !== 'object') return []
  const files = (payload as { files?: unknown }).files
  if (!Array.isArray(files)) return []
  return files.flatMap((entry) => {
    if (!entry || typeof entry !== 'object') return []
    const maybe = entry as {
      relative_path?: unknown
      filename?: unknown
      size_bytes?: unknown
      modified_at?: unknown
      category?: unknown
    }
    if (
      typeof maybe.relative_path !== 'string' ||
      typeof maybe.filename !== 'string' ||
      typeof maybe.size_bytes !== 'number' ||
      typeof maybe.modified_at !== 'string' ||
      typeof maybe.category !== 'string'
    ) {
      return []
    }
    return [
      {
        relative_path: maybe.relative_path,
        filename: maybe.filename,
        size_bytes: maybe.size_bytes,
        modified_at: maybe.modified_at,
        category: maybe.category
      }
    ]
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
  const response = await apiFetch(`${baseUrl}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal
  })

  if (!response.ok) {
    throw await buildHttpError(response, `Stream failed (${response.status})`)
  }
  if (!response.body) {
    throw new Error('Stream response body is empty')
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
    if (sawTerminalEvent) {
      try {
        await reader.cancel()
      } catch {
        // Ignore cancellation errors from already-closing streams.
      }
      break
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
  const response = await apiFetch(`${baseUrl}/scene/${threadId}`, { signal })
  if (!response.ok) {
    throw await buildHttpError(response, `Failed to load scene (${response.status})`)
  }
  return (await response.json()) as SceneInfo
}

export async function getTodos(baseUrl: string, threadId: string): Promise<TodoItem[]> {
  const response = await apiFetch(`${baseUrl}/todos/${threadId}`)
  if (!response.ok) {
    throw await buildHttpError(response, `Failed to load todos (${response.status})`)
  }
  const data = (await response.json()) as { todos?: TodoItem[] }
  return data.todos ?? []
}

export async function getSceneRenders(
  baseUrl: string,
  threadId: string,
  includeLocalWork: boolean = false,
  signal?: AbortSignal
): Promise<RenderImage[]> {
  const query = new URLSearchParams()
  if (includeLocalWork) {
    query.set('include_local_work', 'true')
  }
  const suffix = query.toString() ? `?${query.toString()}` : ''
  const response = await apiFetch(`${baseUrl}/scene/${threadId}/renders${suffix}`, { signal })
  if (!response.ok) {
    throw await buildHttpError(response, `Failed to load renders (${response.status})`)
  }
  const data = (await response.json()) as unknown
  return parseRenderImages(data)
}

export async function getSceneGltf(
  baseUrl: string,
  threadId: string,
  signal?: AbortSignal
): Promise<Blob> {
  const response = await apiFetch(`${baseUrl}/scene/${threadId}/gltf`, { signal })
  if (!response.ok) {
    throw await buildHttpError(response, `Failed to load glTF (${response.status})`)
  }
  return await response.blob()
}

export async function getSceneBlend(
  baseUrl: string,
  threadId: string,
  signal?: AbortSignal
): Promise<Blob> {
  const response = await apiFetch(`${baseUrl}/scene/${threadId}/blend`, { signal })
  if (!response.ok) {
    throw await buildHttpError(response, `Failed to load .blend file (${response.status})`)
  }
  return await response.blob()
}

export async function listSceneBlendFiles(
  baseUrl: string,
  threadId: string,
  signal?: AbortSignal
): Promise<BlendFileEntry[]> {
  const response = await apiFetch(`${baseUrl}/scene/${threadId}/blends`, { signal })
  if (!response.ok) {
    throw await buildHttpError(response, `Failed to load .blend files (${response.status})`)
  }
  const data = (await response.json()) as unknown
  return parseBlendFiles(data)
}

export async function getSceneBlendFile(
  baseUrl: string,
  threadId: string,
  relativePath: string,
  signal?: AbortSignal
): Promise<Blob> {
  const query = new URLSearchParams({ path: relativePath })
  const response = await apiFetch(`${baseUrl}/scene/${threadId}/blends/download?${query.toString()}`, { signal })
  if (!response.ok) {
    throw await buildHttpError(response, `Failed to download .blend file (${response.status})`)
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
  const response = await apiFetch(`${baseUrl}/threads/${threadId}/reference-images`, {
    method: 'POST',
    body: formData
  })
  if (!response.ok) {
    throw await buildHttpError(response, `Failed to upload reference images (${response.status})`)
  }
  const data = (await response.json()) as { images?: ReferenceImage[] }
  return data.images ?? []
}

export async function listReferenceImages(baseUrl: string, threadId: string): Promise<ReferenceImage[]> {
  const response = await apiFetch(`${baseUrl}/threads/${threadId}/reference-images`)
  if (!response.ok) {
    throw await buildHttpError(response, `Failed to load reference images (${response.status})`)
  }
  const data = (await response.json()) as { images?: ReferenceImage[] }
  return data.images ?? []
}

export async function deleteThread(
  baseUrl: string,
  threadId: string,
  signal?: AbortSignal
): Promise<void> {
  try {
    const response = await apiFetch(`${baseUrl}/threads/${threadId}`, {
      method: 'DELETE',
      signal
    })
    if (!response.ok) {
      console.warn(`Backend thread delete returned ${response.status} for ${threadId}`)
    }
  } catch (error) {
    // Best-effort: don't block frontend deletion if backend is unreachable.
    console.warn('Failed to delete thread on backend', error)
  }
}

export async function getHealth(
  baseUrl: string,
  signal?: AbortSignal
): Promise<{ status: string; blender_mode?: 'headless' | 'local-client' }> {
  const response = await apiFetch(`${baseUrl}/health`, { signal })
  if (!response.ok) {
    throw await buildHttpError(response, `Healthcheck failed (${response.status})`)
  }
  return (await response.json()) as { status: string; blender_mode?: 'headless' | 'local-client' }
}


export async function getExamplePrompts(baseUrl: string): Promise<string[]> {
  const response = await apiFetch(`${baseUrl}/example-prompts`)
  if (!response.ok) {
    throw await buildHttpError(response, `Failed to load example prompts (${response.status})`)
  }
  const data = (await response.json()) as { prompts?: string[] }
  return Array.isArray(data.prompts) ? data.prompts : []
}

export async function getMcpTools(
  baseUrl: string,
  threadId: string,
  signal?: AbortSignal
): Promise<McpToolsInfo> {
  const response = await apiFetch(`${baseUrl}/threads/${threadId}/mcp-tools`, { signal })
  if (!response.ok) {
    throw await buildHttpError(response, `Failed to load MCP tools (${response.status})`)
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
  const providerPriority: Record<string, number> = {
    gemini: 0,
    openai: 1,
    anthropic: 2
  }
  const query = threadId ? `?thread_id=${encodeURIComponent(threadId)}` : ''
  const response = await apiFetch(`${baseUrl}/vlm/models${query}`, { signal })
  if (!response.ok) {
    throw await buildHttpError(response, `Failed to load VLM models (${response.status})`)
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
        .sort((a, b) => {
          const aPriority = providerPriority[a.provider] ?? 99
          const bPriority = providerPriority[b.provider] ?? 99
          if (aPriority !== bPriority) {
            return aPriority - bPriority
          }
          return a.display_name.localeCompare(b.display_name)
        })
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

  const backendDefaultProvider =
    typeof data.default_provider === 'string' ? data.default_provider : ''
  const defaultProvider =
    (backendDefaultProvider && providers.some((item) => item.provider === backendDefaultProvider)
      ? backendDefaultProvider
      : providers[0]?.provider) ?? 'gemini'
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

export async function getHeadlessSessionCapacity(
  baseUrl: string,
  signal?: AbortSignal
): Promise<HeadlessSessionCapacityInfo> {
  const response = await apiFetch(`${baseUrl}/headless/session-capacity`, { signal })
  if (!response.ok) {
    throw await buildHttpError(response, `Failed to load headless session capacity (${response.status})`)
  }
  return (await response.json()) as HeadlessSessionCapacityInfo
}

export async function releaseThreadRuntime(
  baseUrl: string,
  threadId: string,
  signal?: AbortSignal
): Promise<ReleaseRuntimeInfo> {
  const response = await apiFetch(`${baseUrl}/threads/${threadId}/release-runtime`, {
    method: 'POST',
    signal
  })
  if (!response.ok) {
    throw await buildHttpError(response, `Failed to release runtime for thread '${threadId}' (${response.status})`)
  }
  return (await response.json()) as ReleaseRuntimeInfo
}
