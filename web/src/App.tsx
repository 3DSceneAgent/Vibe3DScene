import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ApiRequestError,
  streamChat,
  getTodos,
  getSceneRenders,
  getSceneGltf,
  getSceneBlend,
  listSceneBlendFiles,
  getSceneBlendFile,
  deleteThread as deleteThreadApi,
  getHealth,
  renameThreadTitle as renameThreadTitleApi,
  uploadThreadImages,
  listThreadImages,
  getExamplePrompts,
  getMcpTools,
  getVlmModels,
  getHeadlessSessionCapacity,
  releaseThreadRuntime
} from './api/client'
import type {
  BlendFileEntry,
  GraphNodeStream,
  HeadlessSessionCapacityInfo,
  ImageAsset,
  StreamEvent,
  TodoItem,
  VlmProviderOption
} from './api/types'
import { ChatTab } from './components/ChatTab'
import { SceneTab } from './components/SceneTab'
import { SettingsPanel } from './components/SettingsPanel'
import { ThreadList } from './components/ThreadList'
import { loadSettings, loadThreads, loadSettingsAsync, loadThreadsAsync, saveSettings, saveThreads } from './state/storage'
import type { Message, SceneHierarchyNode, Thread } from './state/types'
import {
  applyStreamingDeltaWithId,
  extractAssistantToolCalls,
  extractMessageContent,
  extractMessageThinking,
  extractToolPayload,
  isHumanMessage,
  isToolMessage,
  parseThinking
} from './utils/message'
import type { AssistantToolCall } from './utils/message'
import { downloadBlob } from './utils/download'
import './App.css'

type ThreadLoadingState = {
  scene: boolean
  renders: boolean
  gltf: boolean
  download: boolean
}

function createThreadLoadingState(): ThreadLoadingState {
  return {
    scene: false,
    renders: false,
    gltf: false,
    download: false
  }
}

function formatSceneActionError(error: unknown, actionLabel: string): string {
  if (error instanceof DOMException && error.name === 'AbortError') {
    return `${actionLabel} timed out. Please try again.`
  }
  if (error instanceof Error && error.message.trim()) {
    return `${actionLabel} failed: ${error.message}`
  }
  return `${actionLabel} failed.`
}

function normalizeStreamErrorDetail(error: unknown): string {
  if (error instanceof Error && error.message.trim()) {
    return error.message.trim()
  }
  if (typeof error === 'string' && error.trim()) {
    return error.trim()
  }
  if (error && typeof error === 'object') {
    const detail = (error as { detail?: unknown }).detail
    if (typeof detail === 'string' && detail.trim()) {
      return detail.trim()
    }
    try {
      return JSON.stringify(error)
    } catch {
      return ''
    }
  }
  return ''
}

function formatStreamFailureMessage(error: unknown): string {
  const detail = normalizeStreamErrorDetail(error)
  if (!detail) {
    return 'Sorry, the agent ran into an unexpected issue while processing this request. Please retry.'
  }
  return [
    'Sorry, the agent ran into an error while processing this request.',
    'Please retry, simplify the prompt, or split it into smaller steps.',
    '',
    `Details: ${detail}`
  ].join('\n')
}

function formatThreadCreateError(error: unknown): string {
  if (
    (error instanceof DOMException && error.name === 'AbortError') ||
    (error instanceof Error && error.name === 'AbortError')
  ) {
    return 'Unable to create a new session because the backend took too long to claim a headless runtime after startup.'
  }
  if (error instanceof ApiRequestError) {
    if (error.reason === 'process_capacity_exhausted') {
      const active = Number(error.in_use?.active_headless_sessions)
      const capacity = Number(error.limits?.worker_capacity)
      if (Number.isFinite(active) && Number.isFinite(capacity) && capacity > 0) {
        return `Unable to create a new session because process capacity is exhausted (${active}/${capacity} active).`
      }
      return 'Unable to create a new session because process capacity is exhausted.'
    }
    if (error.reason === 'blender_port_exhausted') {
      const used = Number(error.in_use?.active_headless_sessions)
      const capacity = Number(error.limits?.headless_port_capacity)
      if (Number.isFinite(used) && Number.isFinite(capacity) && capacity > 0) {
        return `Unable to create a new session because the Blender port pool is exhausted (${used}/${capacity} in use).`
      }
      return 'Unable to create a new session because the Blender port pool is exhausted.'
    }
    if (error.reason === 'mcp_port_exhausted') {
      const used = Number(error.in_use?.reserved_mcp_ports)
      const capacity = Number(error.limits?.mcp_port_capacity)
      if (Number.isFinite(used) && Number.isFinite(capacity) && capacity > 0) {
        return `Unable to create a new session because the MCP port pool is exhausted (${used}/${capacity} in use).`
      }
      return 'Unable to create a new session because the MCP port pool is exhausted.'
    }
    if (error.message.trim()) {
      return `Unable to create a new session due to backend resource limits: ${error.message.trim()}`
    }
  }
  if (error instanceof Error && error.message.trim()) {
    return `Unable to create a new session: ${error.message.trim()}`
  }
  return 'Unable to create a new session. Please try again later.'
}

function resolveFastModeHealthState(health: {
  features?: { fast_mode?: boolean }
  defaults?: { fast_mode?: boolean }
}): { available: boolean; defaultEnabled: boolean } {
  const available = health.features?.fast_mode === true
  return {
    available,
    defaultEnabled: available && health.defaults?.fast_mode === true
  }
}

function normalizeThreadTitle(title: string, maxLength: number = 120): string {
  const normalized = title.trim()
  if (normalized.length > maxLength) {
    return normalized.slice(0, maxLength)
  }
  return normalized
}

function buildThreadTitleUpdate(
  thread: Thread,
  nextTitle: string,
  options?: {
    manual?: boolean
  }
): Pick<Thread, 'title' | 'titleEditedManually'> | null {
  const normalized = normalizeThreadTitle(nextTitle)
  if (!normalized) {
    return null
  }
  const nextManualFlag =
    typeof options?.manual === 'boolean'
      ? options.manual
      : Boolean(thread.titleEditedManually)
  if (thread.title === normalized && Boolean(thread.titleEditedManually) === nextManualFlag) {
    return null
  }
  return {
    title: normalized,
    titleEditedManually: nextManualFlag
  }
}

function finalizeStreamingAssistants(messages: Message[], keepAssistantId?: string | null): Message[] {
  let changed = false
  const nextMessages = messages.map((message): Message => {
    if (
      message.role !== 'assistant' ||
      message.status !== 'streaming' ||
      (keepAssistantId != null && message.id === keepAssistantId)
    ) {
      return message
    }
    changed = true
    return { ...message, thinkingActive: false, status: 'final' as const }
  })
  return changed ? nextMessages : messages
}

function finalizeStreamingMessages(messages: Message[], keepAssistantId?: string | null): Message[] {
  let changed = false
  const nextMessages = messages.map((message): Message => {
    if (message.status !== 'streaming') {
      return message
    }
    if (keepAssistantId != null && message.role === 'assistant' && message.id === keepAssistantId) {
      return message
    }
    changed = true
    return { ...message, thinkingActive: false, status: 'final' as const }
  })
  return changed ? nextMessages : messages
}

function getCurrentTurnStartIndex(messages: Message[]): number {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].role === 'user') {
      return index + 1
    }
  }
  return 0
}

function createPendingToolMessage(toolCall: AssistantToolCall, createdAt: number): Message {
  return {
    id: `tool-pending-${toolCall.key}`,
    role: 'tool',
    content: '',
    createdAt,
    status: 'streaming',
    toolCallKey: toolCall.key,
    toolName: toolCall.name
  }
}

function appendPendingToolMessages(messages: Message[], toolCalls: AssistantToolCall[]): Message[] {
  const finalizedMessages = finalizeStreamingAssistants(messages)
  if (toolCalls.length === 0) {
    return finalizedMessages
  }

  const turnStartIndex = getCurrentTurnStartIndex(finalizedMessages)
  const existingKeys = new Set(
    finalizedMessages
      .slice(turnStartIndex)
      .map((message) => message.toolCallKey)
      .filter((key): key is string => typeof key === 'string' && key.length > 0)
  )
  const nextMessages = [...finalizedMessages]
  let changed = finalizedMessages !== messages
  let timestamp = Date.now()

  toolCalls.forEach((toolCall) => {
    if (existingKeys.has(toolCall.key)) return
    existingKeys.add(toolCall.key)
    nextMessages.push(createPendingToolMessage(toolCall, timestamp))
    timestamp += 1
    changed = true
  })

  return changed ? nextMessages : messages
}

function resolveToolMessage(messages: Message[], toolEntry: Message): Message[] {
  const finalizedMessages = finalizeStreamingAssistants(messages)
  const turnStartIndex = getCurrentTurnStartIndex(finalizedMessages)
  let oldestPendingIndex = -1
  let matchingPendingIndex = -1

  for (let index = turnStartIndex; index < finalizedMessages.length; index += 1) {
    const message = finalizedMessages[index]
    if (message.role !== 'tool' || message.status !== 'streaming') {
      continue
    }
    if (oldestPendingIndex === -1) {
      oldestPendingIndex = index
    }
    if (toolEntry.toolName && message.toolName === toolEntry.toolName) {
      matchingPendingIndex = index
      break
    }
  }

  const targetIndex =
    matchingPendingIndex !== -1
      ? matchingPendingIndex
      : !toolEntry.toolName && oldestPendingIndex !== -1
        ? oldestPendingIndex
        : -1

  if (targetIndex === -1) {
    return finalizedMessages === messages ? [...messages, toolEntry] : [...finalizedMessages, toolEntry]
  }

  const target = finalizedMessages[targetIndex]
  const replacement: Message = {
    ...toolEntry,
    id: target.id,
    createdAt: target.createdAt,
    toolCallKey: target.toolCallKey
  }

  return finalizedMessages.map((message, index) => (index === targetIndex ? replacement : message))
}

function App() {
  const REQUEST_TIMEOUT_MS = 35000
  // Claiming a headless runtime can cold-start Blender + MCP on the first request.
  const MCP_REQUEST_TIMEOUT_MS = 30000
  const VLM_REQUEST_TIMEOUT_MS = 10000
  const MAX_EXAMPLE_PROMPTS = 10
  const [threads, setThreads] = useState<Thread[]>(() => loadThreads())
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null)
  const [settings, setSettings] = useState(() => loadSettings())
  const [environment, setEnvironment] = useState<'studio' | 'warm' | 'cool'>('studio')
  const [isStreaming, setIsStreaming] = useState(false)
  const [isSending, setIsSending] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false)
  const [isStorageHydrated, setIsStorageHydrated] = useState(false)
  const [backendStatus, setBackendStatus] = useState<'online' | 'offline' | 'checking'>('checking')
  const [backendMode, setBackendMode] = useState<'headless' | 'local-client' | null>(null)
  const [fastModeAvailable, setFastModeAvailable] = useState(false)
  const [fastModeDefault, setFastModeDefault] = useState(false)
  const [examplePrompts, setExamplePrompts] = useState<string[]>([])
  const [mcpToolsByThread, setMcpToolsByThread] = useState<Record<string, string[]>>({})
  const [mcpToolHintsByThread, setMcpToolHintsByThread] = useState<Record<string, Record<string, string>>>({})
  const [mcpToolsErrorByThread, setMcpToolsErrorByThread] = useState<Record<string, string | null>>({})
  const [mcpToolsLoadingThreadId, setMcpToolsLoadingThreadId] = useState<string | null>(null)
  const [vlmProviders, setVlmProviders] = useState<VlmProviderOption[]>([])
  const [vlmDefaultProvider, setVlmDefaultProvider] = useState<string>('gemini')
  const [vlmDefaultModel, setVlmDefaultModel] = useState<string>('')
  const [vlmErrorByThread, setVlmErrorByThread] = useState<Record<string, string | null>>({})
  const [vlmLoadingThreadId, setVlmLoadingThreadId] = useState<string | null>(null)
  const [loadingByThread, setLoadingByThread] = useState<Record<string, ThreadLoadingState>>({})
  const [sceneActionErrorByThread, setSceneActionErrorByThread] = useState<Record<string, string | null>>({})
  const [streamStatusByThread, setStreamStatusByThread] = useState<Record<string, 'streaming' | 'complete'>>({})
  const [creatingThread, setCreatingThread] = useState(false)
  const [releasingThreadId, setReleasingThreadId] = useState<string | null>(null)
  const [threadCreateError, setThreadCreateError] = useState<string | null>(null)
  const [threadCreateHint, setThreadCreateHint] = useState<string | null>(null)
  const [pendingWelcomePrompt, setPendingWelcomePrompt] = useState<string | null>(null)
  const [headlessQuotaInfo, setHeadlessQuotaInfo] = useState<{ inUse: number; quota: number } | null>(null)
  const streamAbortRef = useRef<AbortController | null>(null)
  const healthAbortRef = useRef<AbortController | null>(null)
  const rendersAbortRef = useRef<Record<string, AbortController>>({})
  const gltfAbortRef = useRef<Record<string, AbortController>>({})
  const previousAssistantContentRef = useRef<string | null>(null)
  const knownStreamIdsRef = useRef<Set<string>>(new Set())
  const knownToolIdsRef = useRef<Set<string>>(new Set())
  const knownToolCallKeysRef = useRef<Set<string>>(new Set())
  const receivedDeltaRef = useRef(false)
  const sceneChangeRef = useRef<Record<string, boolean>>({})
  const settingsRef = useRef(settings)
  const loadedThreadImagesRef = useRef<Set<string>>(new Set())
  const currentStreamRef = useRef<{ threadId: string; assistantId: string; runId: number } | null>(null)
  const streamRunIdRef = useRef(0)
  const messageIdMapRef = useRef<Map<string, string>>(new Map())
  const saveThreadsTimerRef = useRef<number | null>(null)
  const loadingRef = useRef<Record<string, ThreadLoadingState>>({})
  const autoFetchLastRunRef = useRef<Record<string, number>>({})
  const runtimeOccupancyRef = useRef<Record<string, boolean>>({})

  const activeThread = useMemo(
    () => threads.find((thread) => thread.id === activeThreadId) ?? null,
    [threads, activeThreadId]
  )
  const activeThreadLoading = useMemo(
    () => (activeThread ? loadingByThread[activeThread.id] ?? createThreadLoadingState() : createThreadLoadingState()),
    [activeThread, loadingByThread]
  )
  const activeThreadFastMode = useMemo(
    () => Boolean(activeThread?.fastMode ?? fastModeDefault),
    [activeThread, fastModeDefault]
  )
  const activeSceneActionError = useMemo(
    () => (activeThread ? sceneActionErrorByThread[activeThread.id] ?? null : null),
    [activeThread, sceneActionErrorByThread]
  )
  const updateThread = useCallback((threadId: string, updater: (thread: Thread) => Thread) => {
    setThreads((prev) => prev.map((thread) => (thread.id === threadId ? updater(thread) : thread)))
  }, [])

  const setThreadLoading = useCallback((threadId: string, patch: Partial<ThreadLoadingState>) => {
    setLoadingByThread((prev) => {
      const current = prev[threadId] ?? createThreadLoadingState()
      return {
        ...prev,
        [threadId]: {
          ...current,
          ...patch
        }
      }
    })
  }, [])

  const setSceneActionError = useCallback((threadId: string, message: string | null) => {
    setSceneActionErrorByThread((prev) => ({ ...prev, [threadId]: message }))
  }, [])

  const setThreadStreamStatus = useCallback((threadId: string, status: 'streaming' | 'complete') => {
    setStreamStatusByThread((prev) => ({ ...prev, [threadId]: status }))
  }, [])

  const applyHeadlessCapacity = useCallback((capacity: HeadlessSessionCapacityInfo) => {
    const occupancyByThread = new Map(
      capacity.occupying_threads.map((entry) => [entry.thread_id, entry] as const)
    )
    setThreads((prev) => {
      const nextThreads = prev.map((thread) => {
        const occupancy = occupancyByThread.get(thread.id)
        const occupyingResources = Boolean(occupancy?.occupying_resources)
        const nextLastRuntimeActiveMs =
          typeof occupancy?.last_active_ms === 'number' ? occupancy.last_active_ms : thread.lastRuntimeActiveMs
        if (
          thread.occupyingResources === occupyingResources &&
          thread.lastRuntimeActiveMs === nextLastRuntimeActiveMs
        ) {
          return thread
        }
        return {
          ...thread,
          occupyingResources,
          lastRuntimeActiveMs: nextLastRuntimeActiveMs
        }
      })
      const nextRuntimeOccupancy: Record<string, boolean> = {}
      for (const thread of nextThreads) {
        nextRuntimeOccupancy[thread.id] = Boolean(thread.occupyingResources)
      }
      runtimeOccupancyRef.current = nextRuntimeOccupancy
      return nextThreads
    })
    setHeadlessQuotaInfo({
      inUse: Math.max(0, Number(capacity.in_use) || 0),
      quota: Math.max(1, Number(capacity.quota) || 1)
    })
  }, [])

  const refreshHeadlessCapacity = useCallback(
    async (signal?: AbortSignal): Promise<HeadlessSessionCapacityInfo | null> => {
      if (!settings.backendUrl || backendStatus !== 'online' || backendMode !== 'headless') {
        setHeadlessQuotaInfo(null)
        return null
      }
      const capacity = await getHeadlessSessionCapacity(settings.backendUrl, signal)
      applyHeadlessCapacity(capacity)
      return capacity
    },
    [applyHeadlessCapacity, backendMode, backendStatus, settings.backendUrl]
  )

  const releaseThreadRuntimeForThread = useCallback(
    async (
      threadId: string,
      options?: {
        silent?: boolean
      }
    ): Promise<boolean> => {
      if (!settings.backendUrl) {
        return false
      }
      setReleasingThreadId(threadId)
      if (!options?.silent) {
        setThreadCreateHint('Releasing runtime resources for this conversation...')
      }
      try {
        await releaseThreadRuntime(settings.backendUrl, threadId)
        runtimeOccupancyRef.current[threadId] = false
        updateThread(threadId, (thread) => ({
          ...thread,
          occupyingResources: false
        }))
        if (!options?.silent) {
          await refreshHeadlessCapacity()
        }
        if (!options?.silent) {
          setThreadCreateHint('Runtime resources released. You can create a new chat now.')
        }
        return true
      } catch (error) {
        if (!options?.silent) {
          setThreadCreateError(
            error instanceof Error && error.message.trim()
              ? `Failed to release runtime resources: ${error.message.trim()}`
              : 'Failed to release runtime resources.'
          )
        }
        return false
      } finally {
        setReleasingThreadId((current) => (current === threadId ? null : current))
      }
    },
    [refreshHeadlessCapacity, settings.backendUrl, updateThread]
  )

  // Load data from IndexedDB on mount
  useEffect(() => {
    let mounted = true
    const loadData = async () => {
      try {
        const [loadedThreads, loadedSettings] = await Promise.all([
          loadThreadsAsync(),
          loadSettingsAsync()
        ])
        if (mounted) {
          if (loadedThreads.length > 0) {
            setThreads(loadedThreads)
            setActiveThreadId((current) =>
              current && loadedThreads.some((thread) => thread.id === current) ? current : null
            )
          }
          setSettings(loadedSettings)
        }
      } finally {
        if (mounted) {
          setIsStorageHydrated(true)
        }
      }
    }
    void loadData()
    return () => {
      mounted = false
    }
  }, [])

  useEffect(() => {
    if (!isStorageHydrated) {
      return
    }
    if (saveThreadsTimerRef.current !== null) {
      window.clearTimeout(saveThreadsTimerRef.current)
    }
    saveThreadsTimerRef.current = window.setTimeout(() => {
      saveThreads(threads)
      saveThreadsTimerRef.current = null
    }, 400)
    return () => {
      if (saveThreadsTimerRef.current !== null) {
        window.clearTimeout(saveThreadsTimerRef.current)
        saveThreadsTimerRef.current = null
      }
    }
  }, [threads, isStorageHydrated])

  useEffect(() => {
    document.documentElement.dataset.theme = settings.theme
    settingsRef.current = settings
    if (!isStorageHydrated) {
      return
    }
    saveSettings(settings)
  }, [settings, isStorageHydrated])

  useEffect(() => {
    loadingRef.current = loadingByThread
  }, [loadingByThread])

  useEffect(() => {
    const nextRuntimeOccupancy: Record<string, boolean> = {}
    for (const thread of threads) {
      const cached = runtimeOccupancyRef.current[thread.id]
      if (typeof thread.occupyingResources === 'boolean') {
        nextRuntimeOccupancy[thread.id] = thread.occupyingResources
      } else if (typeof cached === 'boolean') {
        nextRuntimeOccupancy[thread.id] = cached
      } else {
        nextRuntimeOccupancy[thread.id] = false
      }
    }
    runtimeOccupancyRef.current = nextRuntimeOccupancy
  }, [threads])

  useEffect(() => {
    let isActive = true

    const checkHealth = async () => {
      if (!settings.backendUrl) {
        if (isActive) {
          setBackendStatus('offline')
          setBackendMode(null)
          setFastModeAvailable(false)
          setFastModeDefault(false)
        }
        return
      }
      if (healthAbortRef.current) {
        healthAbortRef.current.abort()
      }
      const controller = new AbortController()
      healthAbortRef.current = controller
      const timeoutId = window.setTimeout(() => controller.abort(), 3000)
      try {
        const health = await getHealth(settings.backendUrl, controller.signal)
        const fastModeState = resolveFastModeHealthState(health)
        if (isActive) {
          setBackendStatus('online')
          setBackendMode(health.blender_mode ?? null)
          setFastModeAvailable(fastModeState.available)
          setFastModeDefault(fastModeState.defaultEnabled)
        }
      } catch {
        if (isActive) {
          setBackendStatus('offline')
          setBackendMode(null)
          setFastModeAvailable(false)
          setFastModeDefault(false)
        }
      } finally {
        window.clearTimeout(timeoutId)
      }
    }

    setBackendStatus('checking')
    void checkHealth()
    const intervalId = window.setInterval(checkHealth, 10000)
    return () => {
      isActive = false
      if (healthAbortRef.current) {
        healthAbortRef.current.abort()
      }
      window.clearInterval(intervalId)
    }
  }, [settings.backendUrl])

  useEffect(() => {
    let cancelled = false
    if (!settings.backendUrl) {
      setExamplePrompts([])
      return () => {
        cancelled = true
      }
    }
    if (backendStatus !== 'online') {
      return () => {
        cancelled = true
      }
    }
    const fetchPrompts = async () => {
      try {
        const prompts = await getExamplePrompts(settings.backendUrl)
        if (!cancelled) {
          setExamplePrompts(prompts.slice(0, MAX_EXAMPLE_PROMPTS))
        }
      } catch {
        if (!cancelled) {
          setExamplePrompts([])
        }
      }
    }
    void fetchPrompts()
    return () => {
      cancelled = true
    }
  }, [backendStatus, settings.backendUrl, MAX_EXAMPLE_PROMPTS])

  useEffect(() => {
    let cancelled = false
    if (!settings.backendUrl || backendStatus !== 'online' || backendMode !== 'headless') {
      setHeadlessQuotaInfo(null)
      setThreadCreateHint(null)
      setThreads((prev) =>
        prev.map((thread) =>
          thread.occupyingResources
            ? {
                ...thread,
                occupyingResources: false
              }
            : thread
        )
      )
      return () => {
        cancelled = true
      }
    }

    const refresh = async () => {
      try {
        const capacity = await getHeadlessSessionCapacity(settings.backendUrl)
        if (cancelled) return
        applyHeadlessCapacity(capacity)
      } catch {
        if (!cancelled) {
          setHeadlessQuotaInfo(null)
        }
      }
    }

    void refresh()
    const intervalId = window.setInterval(() => {
      void refresh()
    }, 6000)

    return () => {
      cancelled = true
      window.clearInterval(intervalId)
    }
  }, [applyHeadlessCapacity, backendMode, backendStatus, settings.backendUrl])

  useEffect(() => {
    if (!settings.backendUrl || backendStatus !== 'online' || backendMode !== 'headless') {
      return () => undefined
    }
    const refreshOnForeground = () => {
      void refreshHeadlessCapacity()
    }
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        refreshOnForeground()
      }
    }
    window.addEventListener('focus', refreshOnForeground)
    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => {
      window.removeEventListener('focus', refreshOnForeground)
      document.removeEventListener('visibilitychange', handleVisibilityChange)
    }
  }, [backendMode, backendStatus, refreshHeadlessCapacity, settings.backendUrl])

  useEffect(() => {
    let cancelled = false
    const threadId = activeThread?.id
    if (!threadId || !settings.backendUrl || backendStatus !== 'online') {
      return () => {
        cancelled = true
      }
    }

    const controller = new AbortController()
    const timeoutId = window.setTimeout(() => controller.abort(), VLM_REQUEST_TIMEOUT_MS)
    setVlmLoadingThreadId(threadId)
    const fetchVlmConfig = async () => {
      try {
        const modelInfo = await getVlmModels(settings.backendUrl, threadId, controller.signal)
        if (cancelled) return
        setVlmProviders(modelInfo.providers)
        setVlmDefaultProvider(modelInfo.default_provider)
        setVlmDefaultModel(modelInfo.default_model)
        setVlmErrorByThread((prev) => ({ ...prev, [threadId]: null }))
        updateThread(threadId, (thread) => {
          const fallbackProvider =
            modelInfo.default_provider || modelInfo.providers[0]?.provider || thread.vlmProvider || 'gemini'
          const isFreshThread = thread.messages.length === 0
          const preferredProvider = isFreshThread
            ? modelInfo.thread_selection?.provider ||
              modelInfo.default_provider ||
              thread.vlmProvider ||
              fallbackProvider
            : thread.vlmProvider || modelInfo.thread_selection?.provider || fallbackProvider
          const selectedProvider =
            modelInfo.providers.find((item) => item.provider === preferredProvider)?.provider || fallbackProvider
          const providerOption = modelInfo.providers.find((item) => item.provider === selectedProvider)
          const providerModels = providerOption?.models ?? []
          let nextModel =
            (isFreshThread
              ? modelInfo.thread_selection?.model || providerOption?.default_model || modelInfo.default_model
              : thread.vlmModel || modelInfo.thread_selection?.model || providerOption?.default_model || modelInfo.default_model) ||
            thread.vlmModel
          if (!nextModel || (providerModels.length > 0 && !providerModels.includes(nextModel))) {
            nextModel = providerOption?.default_model ?? providerModels[0] ?? nextModel
          }
          return {
            ...thread,
            vlmProvider: selectedProvider,
            vlmModel: nextModel,
            vlmLocked: Boolean(modelInfo.thread_selection?.locked)
          }
        })
      } catch (error) {
        if (cancelled) return
        setVlmErrorByThread((prev) => ({
          ...prev,
          [threadId]:
            error instanceof DOMException && error.name === 'AbortError'
              ? 'Timed out while loading VLM models'
              : error instanceof Error
                ? error.message
                : 'Failed to load VLM models'
        }))
      } finally {
        window.clearTimeout(timeoutId)
        if (!cancelled) {
          setVlmLoadingThreadId((current) => (current === threadId ? null : current))
        }
      }
    }
    void fetchVlmConfig()
    return () => {
      cancelled = true
      controller.abort()
      window.clearTimeout(timeoutId)
    }
  }, [activeThread?.id, settings.backendUrl, backendStatus, updateThread, VLM_REQUEST_TIMEOUT_MS])

  useEffect(() => {
    let cancelled = false
    const threadId = activeThread?.id
    if (!threadId || !settings.backendUrl || backendStatus !== 'online') {
      return () => {
        cancelled = true
      }
    }

    const fetchThreadTodos = async () => {
      try {
        const todos = await getTodos(settings.backendUrl, threadId)
        if (cancelled) return
        updateThread(threadId, (thread) => ({
          ...thread,
          todos
        }))
      } catch {
        // Keep existing local todo state if the background refresh fails.
      }
    }

    void fetchThreadTodos()
    return () => {
      cancelled = true
    }
  }, [activeThread?.id, backendStatus, settings.backendUrl, updateThread])

  const createThread = async () => {
    if (creatingThread || releasingThreadId !== null) {
      return
    }
    setThreadCreateError(null)
    setThreadCreateHint(null)
    setCreatingThread(true)

    try {
      const nextThreadId = `thread-${crypto.randomUUID()}`
      const initialProvider = vlmDefaultProvider || vlmProviders[0]?.provider
      const initialProviderOption = vlmProviders.find((item) => item.provider === initialProvider)
      const initialModel = initialProviderOption?.default_model || vlmDefaultModel
      const newThread: Thread = {
        id: nextThreadId,
        title: 'New chat',
        titleEditedManually: false,
        createdAt: Date.now(),
        messages: [],
        mcpToolEnabled: {},
        fastMode: fastModeAvailable ? fastModeDefault : undefined,
        vlmProvider: initialProvider,
        vlmModel: initialModel,
        vlmLocked: false,
        todos: [],
        scene: null,
        renders: [],
        gltfUrl: null,
        sceneHierarchy: [],
        sceneHasChange: false,
        images: [],
        graphEvents: [],
        occupyingResources: false,
        lastRuntimeActiveMs: 0
      }
      setThreads((prev) => [newThread, ...prev])
      runtimeOccupancyRef.current[nextThreadId] = false
      setActiveThreadId(newThread.id)
      setThreadCreateHint(null)
    } catch (error) {
      setThreadCreateHint(null)
      setThreadCreateError(formatThreadCreateError(error))
    } finally {
      setCreatingThread(false)
    }
  }

  const deleteThread = (threadId: string) => {
    // Fire-and-forget backend cleanup (kills Blender/MCP processes, releases ports).
    if (settings.backendUrl) {
      void deleteThreadApi(settings.backendUrl, threadId)
    }

    setThreads((prev) => {
      const target = prev.find((thread) => thread.id === threadId)
      if (target?.gltfUrl) {
        URL.revokeObjectURL(target.gltfUrl)
      }
      if (target?.images) {
        target.images.forEach((image) => {
          if (image.previewUrl) {
            URL.revokeObjectURL(image.previewUrl)
          }
        })
      }
      return prev.filter((thread) => thread.id !== threadId)
    })
    loadedThreadImagesRef.current.delete(threadId)
    delete autoFetchLastRunRef.current[threadId]
    delete sceneChangeRef.current[threadId]
    if (rendersAbortRef.current[threadId]) {
      rendersAbortRef.current[threadId].abort()
      delete rendersAbortRef.current[threadId]
    }
    if (gltfAbortRef.current[threadId]) {
      gltfAbortRef.current[threadId].abort()
      delete gltfAbortRef.current[threadId]
    }
    setLoadingByThread((prev) => {
      const next = { ...prev }
      delete next[threadId]
      return next
    })
    setSceneActionErrorByThread((prev) => {
      const next = { ...prev }
      delete next[threadId]
      return next
    })
    setMcpToolsByThread((prev) => {
      const next = { ...prev }
      delete next[threadId]
      return next
    })
    setMcpToolHintsByThread((prev) => {
      const next = { ...prev }
      delete next[threadId]
      return next
    })
    setMcpToolsErrorByThread((prev) => {
      const next = { ...prev }
      delete next[threadId]
      return next
    })
    setVlmErrorByThread((prev) => {
      const next = { ...prev }
      delete next[threadId]
      return next
    })
    setMcpToolsLoadingThreadId((current) => (current === threadId ? null : current))
    setVlmLoadingThreadId((current) => (current === threadId ? null : current))
    delete runtimeOccupancyRef.current[threadId]
    if (activeThreadId === threadId) {
      setActiveThreadId(null)
    }
    if (backendStatus === 'online' && backendMode === 'headless' && settings.backendUrl) {
      void refreshHeadlessCapacity()
    }
  }

  const deleteAllThreads = () => {
    if (streamAbortRef.current) {
      streamAbortRef.current.abort()
    }
    if (currentStreamRef.current) {
      currentStreamRef.current = null
    }
    setIsStreaming(false)
    setIsSending(false)
    messageIdMapRef.current.clear()
    knownToolCallKeysRef.current.clear()

    setThreads((prev) => {
      for (const thread of prev) {
        if (settings.backendUrl) {
          void deleteThreadApi(settings.backendUrl, thread.id)
        }
        if (thread.gltfUrl) URL.revokeObjectURL(thread.gltfUrl)
        if (thread.images) {
          thread.images.forEach((img) => {
            if (img.previewUrl) URL.revokeObjectURL(img.previewUrl)
          })
        }
      }
      return []
    })

    loadedThreadImagesRef.current.clear()
    autoFetchLastRunRef.current = {}
    sceneChangeRef.current = {}
    for (const ctrl of Object.values(rendersAbortRef.current)) ctrl.abort()
    rendersAbortRef.current = {}
    for (const ctrl of Object.values(gltfAbortRef.current)) ctrl.abort()
    gltfAbortRef.current = {}
    runtimeOccupancyRef.current = {}
    setLoadingByThread({})
    setSceneActionErrorByThread({})
    setStreamStatusByThread({})
    setMcpToolsByThread({})
    setMcpToolHintsByThread({})
    setMcpToolsErrorByThread({})
    setVlmErrorByThread({})
    setMcpToolsLoadingThreadId(null)
    setVlmLoadingThreadId(null)
    setActiveThreadId(null)

    if (backendStatus === 'online' && backendMode === 'headless' && settings.backendUrl) {
      void refreshHeadlessCapacity()
    }
  }

  const mergeTodos = (existing: TodoItem[], incoming: TodoItem[]) => {
    const map = new Map(existing.map((todo) => [todo.id, todo]))
    incoming.forEach((todo) => map.set(todo.id, todo))
    return Array.from(map.values())
  }

  const mergeThreadImages = (
    existing: ImageAsset[],
    incoming: ImageAsset[]
  ): ImageAsset[] => {
    const map = new Map(existing.map((image) => [image.id, image]))
    incoming.forEach((image) => {
      const previous = map.get(image.id)
      map.set(image.id, previous ? { ...image, previewUrl: previous.previewUrl } : image)
    })
    return Array.from(map.values())
  }

  const handleVlmSelectionChange = useCallback(
    (provider: string, model: string) => {
      if (!activeThreadId) return
      updateThread(activeThreadId, (thread) => {
        const providerOption = vlmProviders.find(
          (item) => item.provider === provider && item.configured
        )
        if (!providerOption) {
          return thread
        }
        const allowedModels =
          providerOption.models.length > 0
            ? providerOption.models
            : [providerOption.default_model]
        if (!allowedModels.includes(model)) {
          return thread
        }
        return {
          ...thread,
          vlmProvider: providerOption.provider,
          vlmModel: model
        }
      })
    },
    [activeThreadId, updateThread, vlmProviders]
  )

  const handleMcpToolToggle = useCallback(
    (toolName: string, enabled: boolean) => {
      if (!activeThreadId) return
      updateThread(activeThreadId, (thread) => ({
        ...thread,
        mcpToolEnabled: {
          ...(thread.mcpToolEnabled ?? {}),
          [toolName]: enabled
        }
      }))
    },
    [activeThreadId, updateThread]
  )

  const handleFastModeToggle = useCallback(
    (enabled: boolean) => {
      if (!activeThreadId) return
      updateThread(activeThreadId, (thread) => ({
        ...thread,
        fastMode: enabled
      }))
    },
    [activeThreadId, updateThread]
  )

  const handleSceneHierarchyChange = useCallback(
    (threadId: string, hierarchy: SceneHierarchyNode[]) => {
      updateThread(threadId, (thread) => ({
        ...thread,
        sceneHierarchy: hierarchy
      }))
    },
    [updateThread]
  )

  const renameThread = useCallback(
    (threadId: string, nextTitle: string) => {
      const thread = threads.find((entry) => entry.id === threadId)
      if (!thread) {
        return
      }
      const titleUpdate = buildThreadTitleUpdate(thread, nextTitle, { manual: true })
      if (!titleUpdate) {
        return
      }
      updateThread(threadId, (current) => ({
        ...current,
        ...titleUpdate
      }))
      if (!settings.backendUrl) {
        return
      }
      void renameThreadTitleApi(settings.backendUrl, threadId, titleUpdate.title).catch((error) => {
        console.warn(`Failed to sync renamed thread title for ${threadId}`, error)
      })
    },
    [settings.backendUrl, threads, updateThread]
  )

  const handleStop = useCallback(() => {
    if (streamAbortRef.current) {
      streamAbortRef.current.abort()
    }
    
    if (currentStreamRef.current) {
      const { threadId, runId } = currentStreamRef.current
      updateThread(threadId, (thread) => {
        const messages = finalizeStreamingMessages(thread.messages)
        return messages === thread.messages ? thread : { ...thread, messages }
      })
      setThreadStreamStatus(threadId, 'complete')
      if (streamRunIdRef.current === runId) {
        streamRunIdRef.current += 1
      }
      currentStreamRef.current = null
    }
    
    messageIdMapRef.current.clear()
    knownToolCallKeysRef.current.clear()
    setIsStreaming(false)
    setIsSending(false)
  }, [setThreadStreamStatus, updateThread])

  const handleSend = async (text: string, files: File[] = []) => {
    if (!activeThread) {
      return false
    }
    const threadId = activeThread.id
    let hasMcpToolSnapshot =
      Object.prototype.hasOwnProperty.call(mcpToolsByThread, threadId) &&
      !mcpToolsErrorByThread[threadId]
    let availableMcpTools = mcpToolsByThread[threadId] ?? []
    const selectedProvider =
      activeThread.vlmProvider || vlmDefaultProvider || vlmProviders[0]?.provider || undefined
    const selectedProviderOption = selectedProvider
      ? vlmProviders.find((item) => item.provider === selectedProvider)
      : undefined
    const selectedModel =
      activeThread.vlmModel ||
      selectedProviderOption?.default_model ||
      vlmDefaultModel ||
      vlmProviders[0]?.default_model ||
      undefined
    let attachedImageIds: string[] | undefined
    const now = Date.now()
    const userMessage: Message = {
      id: `msg-${now}-user`,
      role: 'user',
      content: text,
      createdAt: now
    }
    const assistantId = `msg-${now}-assistant`
    const assistantMessage: Message = {
      id: assistantId,
      role: 'assistant',
      content: '',
      thinkingActive: false,
      createdAt: now,
      streamId: null,
      status: 'streaming'
    }
    const markSendFailed = (message: string) => {
      updateThread(threadId, (thread) => {
        const hasPlaceholder = thread.messages.some((item) => item.id === assistantId)
        if (!hasPlaceholder) {
          return {
            ...thread,
            messages: [
              ...thread.messages,
              {
                id: `msg-${Date.now()}-runtime-preflight-error`,
                role: 'assistant',
                content: `Unable to send message: ${message}`,
                createdAt: Date.now(),
                status: 'error'
              }
            ]
          }
        }
        return {
          ...thread,
          messages: thread.messages.map((item) =>
            item.id === assistantId
              ? {
                  ...item,
                  content: `Unable to send message: ${message}`,
                  thinkingActive: false,
                  status: 'error'
                }
              : item
          )
        }
      })
      setThreadStreamStatus(threadId, 'complete')
      setIsSending(false)
      setIsStreaming(false)
    }

    setIsSending(true)
    setThreadStreamStatus(threadId, 'streaming')
    updateThread(threadId, (thread) => {
      const shouldAutoTitle =
        !thread.titleEditedManually && (thread.title === 'New chat' || thread.messages.length === 0)
      const titleUpdate = shouldAutoTitle ? buildThreadTitleUpdate(thread, text.slice(0, 32)) : null
      return {
        ...thread,
        ...(titleUpdate ?? {}),
        vlmProvider: selectedProvider || thread.vlmProvider,
        vlmModel: selectedModel || thread.vlmModel,
        vlmLocked: thread.vlmLocked ?? false,
        graphEvents: [],
        messages: [...thread.messages, userMessage, assistantMessage]
      }
    })

    if (files.length > 0) {
      if (!settings.backendUrl) {
        markSendFailed('Backend is offline.')
        return false
      }
      try {
        const uploaded = await uploadThreadImages(settings.backendUrl, threadId, files)
        attachedImageIds = uploaded
          .map((image) => image.id)
          .filter((imageId): imageId is string => typeof imageId === 'string' && imageId.length > 0)
        const nextImages = uploaded.map((image, index) => ({
          ...image,
          previewUrl: files[index] ? URL.createObjectURL(files[index]) : undefined
        }))
        updateThread(threadId, (thread) => ({
          ...thread,
          images: mergeThreadImages(thread.images ?? [], nextImages)
        }))
      } catch (error) {
        const detail =
          error instanceof Error && error.message.trim()
            ? `Failed to upload images: ${error.message.trim()}`
            : 'Failed to upload images.'
        markSendFailed(detail)
        return false
      }
    }

    let resolvedFastModeAvailable = fastModeAvailable
    let resolvedFastModeDefault = fastModeDefault
    try {
      let resolvedBackendStatus = backendStatus
      let resolvedBackendMode = backendMode
      if ((resolvedBackendStatus === 'checking' || !resolvedFastModeAvailable) && settings.backendUrl) {
        try {
          const health = await getHealth(settings.backendUrl)
          const fastModeState = resolveFastModeHealthState(health)
          setBackendStatus('online')
          setBackendMode(health.blender_mode ?? null)
          setFastModeAvailable(fastModeState.available)
          setFastModeDefault(fastModeState.defaultEnabled)
          resolvedBackendStatus = 'online'
          resolvedBackendMode = health.blender_mode ?? null
          resolvedFastModeAvailable = fastModeState.available
          resolvedFastModeDefault = fastModeState.defaultEnabled
        } catch {
          setBackendStatus('offline')
          setBackendMode(null)
          setFastModeAvailable(false)
          setFastModeDefault(false)
          resolvedBackendStatus = 'offline'
          resolvedBackendMode = null
          resolvedFastModeAvailable = false
          resolvedFastModeDefault = false
        }
      }

      if (!settings.backendUrl || resolvedBackendStatus !== 'online') {
        throw new Error('Backend is offline.')
      }

      if (resolvedBackendMode === 'headless') {
        const loadCapacitySnapshot = async () => {
          const snapshot = await getHeadlessSessionCapacity(settings.backendUrl)
          applyHeadlessCapacity(snapshot)
          return snapshot
        }
        const isCurrentThreadOccupying = (capacity: HeadlessSessionCapacityInfo): boolean =>
          capacity.occupying_threads.some(
            (entry) => entry.thread_id === threadId && entry.occupying_resources
          )

        const capacity = await loadCapacitySnapshot()
        if (!isCurrentThreadOccupying(capacity) && capacity.in_use >= capacity.quota) {
          const oldestOccupiedThread = [...capacity.occupying_threads]
            .filter((entry) => entry.thread_id !== threadId)
            .sort((a, b) => a.last_active_ms - b.last_active_ms)[0]
          if (!oldestOccupiedThread) {
            throw new Error(
              `Runtime quota is full (${capacity.in_use}/${capacity.quota}) and no occupied thread can be released.`
            )
          }
          const released = await releaseThreadRuntimeForThread(oldestOccupiedThread.thread_id, {
            silent: true
          })
          if (!released) {
            throw new Error('Failed to release an old occupied runtime slot automatically.')
          }
          const refreshed = await loadCapacitySnapshot()
          if (!isCurrentThreadOccupying(refreshed) && refreshed.in_use >= refreshed.quota) {
            throw new Error(
              `Runtime quota is still full after auto-release (${refreshed.in_use}/${refreshed.quota}).`
            )
          }
        }

        const mcpTimeoutController = new AbortController()
        const mcpTimeoutId = window.setTimeout(() => mcpTimeoutController.abort(), MCP_REQUEST_TIMEOUT_MS)
        setMcpToolsLoadingThreadId(threadId)
        try {
          const toolInfo = await getMcpTools(settings.backendUrl, threadId, mcpTimeoutController.signal)
          runtimeOccupancyRef.current[threadId] = true
          availableMcpTools = toolInfo.tools
          hasMcpToolSnapshot = true
          setMcpToolsByThread((prev) => ({ ...prev, [threadId]: toolInfo.tools }))
          setMcpToolHintsByThread((prev) => ({ ...prev, [threadId]: toolInfo.tool_hints }))
          setMcpToolsErrorByThread((prev) => ({ ...prev, [threadId]: null }))
        } catch (error) {
          setMcpToolsByThread((prev) => ({ ...prev, [threadId]: [] }))
          setMcpToolHintsByThread((prev) => ({ ...prev, [threadId]: {} }))
          setMcpToolsErrorByThread((prev) => ({
            ...prev,
            [threadId]:
              error instanceof DOMException && error.name === 'AbortError'
                ? 'Timed out while claiming runtime for this conversation.'
                : error instanceof Error
                  ? error.message
                  : 'Failed to claim runtime for this conversation.'
          }))
          throw error
        } finally {
          window.clearTimeout(mcpTimeoutId)
          setMcpToolsLoadingThreadId((current) => (current === threadId ? null : current))
        }
        const latestCapacity = await loadCapacitySnapshot()
        const latestThreadEntry = latestCapacity.occupying_threads.find((entry) => entry.thread_id === threadId)
        const occupyingAfterClaim = latestThreadEntry
          ? Boolean(latestThreadEntry.occupying_resources)
          : true
        runtimeOccupancyRef.current[threadId] = occupyingAfterClaim
        updateThread(threadId, (thread) => ({
          ...thread,
          occupyingResources: occupyingAfterClaim,
          lastRuntimeActiveMs:
            typeof latestThreadEntry?.last_active_ms === 'number'
              ? latestThreadEntry.last_active_ms
              : thread.lastRuntimeActiveMs
        }))
      }
    } catch (error) {
      const message = formatThreadCreateError(error)
      setMcpToolsErrorByThread((prev) => ({ ...prev, [threadId]: message }))
      markSendFailed(message)
      return false
    }

    const enabledMcpTools = hasMcpToolSnapshot
      ? availableMcpTools.filter((toolName) => activeThread.mcpToolEnabled?.[toolName] !== false)
      : undefined
    const requestedFastMode = resolvedFastModeAvailable
      ? Boolean(activeThread.fastMode ?? resolvedFastModeDefault)
      : false

    if (streamAbortRef.current) {
      streamAbortRef.current.abort()
    }
    if (currentStreamRef.current) {
      const { threadId: previousThreadId } = currentStreamRef.current
      updateThread(previousThreadId, (thread) => {
        const messages = finalizeStreamingMessages(thread.messages)
        return messages === thread.messages ? thread : { ...thread, messages }
      })
      setThreadStreamStatus(previousThreadId, 'complete')
      currentStreamRef.current = null
    }

    previousAssistantContentRef.current =
      activeThread.messages.slice().reverse().find((message) => message.role === 'assistant')?.content ?? null
    knownStreamIdsRef.current = new Set(
      activeThread.messages
        .map((message) => message.streamId)
        .filter((streamId): streamId is string => typeof streamId === 'string' && streamId.length > 0)
    )
    knownToolIdsRef.current = new Set(
      activeThread.messages
        .filter((message) => message.role === 'tool')
        .map((message) => message.id)
        .filter((id): id is string => typeof id === 'string' && id.length > 0)
    )
    knownToolCallKeysRef.current.clear()
    receivedDeltaRef.current = false

    setIsStreaming(true)
    const runId = streamRunIdRef.current + 1
    streamRunIdRef.current = runId
    const abortController = new AbortController()
    streamAbortRef.current = abortController
    currentStreamRef.current = { threadId, assistantId, runId }
    messageIdMapRef.current.clear()
    messageIdMapRef.current.set('initial', assistantId)
    let streamErrorAppended = false
    const appendStreamErrorMessage = (error: unknown) => {
      if (streamErrorAppended) {
        return
      }
      streamErrorAppended = true
      const friendlyError = formatStreamFailureMessage(error)
      updateThread(threadId, (thread) => {
        const finalizedMessages: Message[] = thread.messages.map((message): Message => (
          message.status === 'streaming'
            ? { ...message, thinkingActive: false, status: 'final' as const }
            : message
        ))
        const errorMessage: Message = {
          id: `msg-${Date.now()}-${Math.random()}-stream-error`,
          role: 'assistant',
          content: friendlyError,
          createdAt: Date.now(),
          status: 'error'
        }
        return {
          ...thread,
          messages: [...finalizedMessages, errorMessage]
        }
      })
    }
    const handleStreamEvent = (event: StreamEvent) => {
      if (streamRunIdRef.current !== runId) {
        return
      }
      if (event.event === 'done') {
        setThreadStreamStatus(threadId, 'complete')
      }
      if (event.event === 'graph_node' && event.graph_node) {
        const graphEvent: GraphNodeStream = event.graph_node
        updateThread(threadId, (thread) => {
          const nextEvents = [...(thread.graphEvents ?? []), graphEvent]
          return {
            ...thread,
            graphEvents: nextEvents.slice(-200)
          }
        })
      }
      const getOrCreateAssistantMessage = (messageId: string | null): string => {
        if (!messageId) {
          return assistantId
        }
        const existingId = messageIdMapRef.current.get(messageId)
        if (existingId) {
          return existingId
        }

        const initialId = messageIdMapRef.current.get('initial')
        if (initialId) {
          messageIdMapRef.current.set(messageId, initialId)
          messageIdMapRef.current.delete('initial')
          updateThread(threadId, (thread) => ({
            ...thread,
            messages: thread.messages.map((message) =>
              message.id === initialId && !message.streamId
                ? { ...message, streamId: messageId }
                : message
            )
          }))
          return initialId
        }

        const newAssistantId = `msg-${Date.now()}-${Math.random()}-assistant`
        messageIdMapRef.current.set(messageId, newAssistantId)
        const newMessage: Message = {
          id: newAssistantId,
          role: 'assistant',
          content: '',
          thinkingActive: false,
          createdAt: Date.now(),
          streamId: messageId,
          status: 'streaming'
        }
        updateThread(threadId, (thread) => ({
          ...thread,
          messages: [...finalizeStreamingAssistants(thread.messages), newMessage]
        }))
        return newAssistantId
      }

      const updateAssistantById = (msgId: string, updater: (message: Message) => Message) => {
        updateThread(threadId, (thread) => ({
          ...thread,
          messages: thread.messages.map((message) =>
            message.id === msgId ? updater(message) : message
          )
        }))
      }

      const updateSceneChange = (nextValue: boolean, isFinal: boolean) => {
        const current = sceneChangeRef.current[threadId] ?? false
        const next = isFinal ? nextValue : nextValue || current
        sceneChangeRef.current[threadId] = next
        updateThread(threadId, (thread) => ({
          ...thread,
          sceneHasChange: next
        }))
        return next
      }

      if (event.error) {
        appendStreamErrorMessage(event.error)
        setThreadStreamStatus(threadId, 'complete')
        return
      }

      if (event.todos && event.todos.length > 0) {
        updateThread(threadId, (thread) => ({
          ...thread,
          todos: mergeTodos(thread.todos, event.todos || [])
        }))
      }

      if (typeof event.scene_has_change === 'boolean') {
        const isFinalEvent = event.event === 'done'
        const nextSceneChange = updateSceneChange(event.scene_has_change, isFinalEvent)
        if (nextSceneChange) {
          triggerAutoFetch(threadId, isFinalEvent)
        }
        if (isFinalEvent) {
          sceneChangeRef.current[threadId] = false
          updateThread(threadId, (thread) => ({
            ...thread,
            sceneHasChange: false
          }))
        }
      }

      if (event.thinking_delta) {
        const targetAssistantId = getOrCreateAssistantMessage(event.message_id ?? null)
        updateAssistantById(targetAssistantId, (message) => ({
          ...message,
          thinking: `${message.thinking ?? ''}${event.thinking_delta ?? ''}`,
          thinkingActive: true,
          streamId: event.message_id ?? message.streamId ?? null,
          status: 'streaming'
        }))
      }

      if (event.delta) {
        receivedDeltaRef.current = true
        if (event.message_id) {
          knownStreamIdsRef.current.add(event.message_id)
        }
        const targetAssistantId = getOrCreateAssistantMessage(event.message_id ?? null)
        updateAssistantById(targetAssistantId, (message) => {
          const next = applyStreamingDeltaWithId(
            message.raw,
            event.delta || '',
            message.streamId,
            event.message_id ?? null
          )
          return {
            ...message,
            content: next.text,
            // Some providers stream reasoning separately from visible text.
            // Preserve previously received thinking when the text delta itself carries none.
            thinking: next.thinking ?? message.thinking,
            thinkingActive: false,
            raw: next.raw,
            streamId: next.messageId,
            status: 'streaming'
          }
        })
        return
      }

      if (event.messages && event.messages.length > 0) {
        const assistantToolCalls = event.messages
          .filter((message) => !isHumanMessage(message) && !isToolMessage(message))
          .flatMap((message) => extractAssistantToolCalls(message))
          .filter((toolCall) => {
            if (knownToolCallKeysRef.current.has(toolCall.key)) {
              return false
            }
            knownToolCallKeysRef.current.add(toolCall.key)
            return true
          })
        if (assistantToolCalls.length > 0) {
          updateThread(threadId, (thread) => {
            const nextMessages = appendPendingToolMessages(thread.messages, assistantToolCalls)
            return nextMessages === thread.messages ? thread : { ...thread, messages: nextMessages }
          })
        }

        const toolEntries = event.messages
          .filter((message) => isToolMessage(message))
          .map((message) => {
            const toolId =
              typeof message === 'object' && message !== null && 'id' in message
                ? (message as { id?: string | null }).id ?? null
                : null
            if (toolId && knownToolIdsRef.current.has(toolId)) {
              return null
            }
            if (toolId) {
              knownToolIdsRef.current.add(toolId)
            }
            const extracted = extractToolPayload(message)
            const payload = extracted?.payload ?? message
            const content = extractMessageContent(payload)
            const toolEntry: Message = {
              id: toolId ?? `tool-${Date.now()}-${Math.random()}`,
              role: 'tool',
              content,
              createdAt: Date.now(),
              toolName: extracted?.name,
              toolPayload: payload,
              toolMedia: extracted?.media ?? [],
              status: 'final'
            }
            return toolEntry
          })
          .filter((entry): entry is Message => entry !== null)
        if (toolEntries.length > 0) {
          updateThread(threadId, (thread) => ({
            ...thread,
            messages: toolEntries.reduce((currentMessages, toolEntry) => resolveToolMessage(currentMessages, toolEntry), thread.messages)
          }))
        }

        const candidates = event.messages.filter(
          (message) => !isHumanMessage(message) && !isToolMessage(message)
        )
        if (candidates.length === 0) return
        let selected: (typeof candidates)[number] | null = null
        for (let i = candidates.length - 1; i >= 0; i -= 1) {
          const candidate = candidates[i]
          const candidateId =
            typeof candidate === 'object' && candidate !== null && 'id' in candidate
              ? (candidate as { id?: string | null }).id ?? null
              : null
          if (candidateId && knownStreamIdsRef.current.has(candidateId)) {
            continue
          }
          selected = candidate
          break
        }
        if (!selected) return
        const raw = extractMessageContent(selected)
        const providerThinking = extractMessageThinking(selected)
        if (!raw && !providerThinking) return
        const parsed = raw ? parseThinking(raw) : { text: '', thinking: undefined }
        const streamId =
          typeof selected === 'object' && selected !== null && 'id' in selected
            ? (selected as { id?: string | null }).id ?? null
            : null
        if (streamId && knownStreamIdsRef.current.has(streamId)) {
          return
        }
        if (!receivedDeltaRef.current && previousAssistantContentRef.current === parsed.text) {
          return
        }
        if (streamId) {
          knownStreamIdsRef.current.add(streamId)
        }
        const targetAssistantId = getOrCreateAssistantMessage(streamId)
        updateAssistantById(targetAssistantId, (message) => ({
          ...message,
          content: parsed.text,
          thinking: providerThinking || parsed.thinking || message.thinking,
          thinkingActive: false,
          raw,
          streamId,
          status: 'streaming'
        }))
      }
    }

    let streamPromise: Promise<void>
    try {
      streamPromise = streamChat({
        baseUrl: settings.backendUrl,
        message: text,
        threadId,
        enabledMcpTools,
        fastMode: requestedFastMode,
        attachedImageIds,
        vlmProvider: selectedProvider,
        vlmModel: selectedModel,
        signal: abortController.signal,
        onEvent: handleStreamEvent
      })
    } catch (error) {
      appendStreamErrorMessage(error)
      setThreadStreamStatus(threadId, 'complete')
      setIsStreaming(false)
      setIsSending(false)
      return false
    }
    streamPromise
      .catch((error) => {
        if (streamRunIdRef.current !== runId) {
          return
        }
        appendStreamErrorMessage(error)
        setThreadStreamStatus(threadId, 'complete')
      })
      .finally(() => {
        if (streamRunIdRef.current !== runId) {
          return
        }
        updateThread(threadId, (thread) => ({
          ...thread,
          messages: thread.messages.map((message) =>
            message.status === 'streaming'
              ? { ...message, thinkingActive: false, status: 'final' }
              : message
            )
        }))
        setThreadStreamStatus(threadId, 'complete')
        setIsStreaming(false)
        setIsSending(false)
        if (streamAbortRef.current === abortController) {
          streamAbortRef.current = null
        }
        if (currentStreamRef.current?.runId === runId) {
          currentStreamRef.current = null
        }
        messageIdMapRef.current.clear()
      })
    return true
  }

  const isHeadlessRuntimeClaimed = useCallback(
    (threadId: string): boolean => {
      if (backendStatus !== 'online' || backendMode !== 'headless') {
        return true
      }
      const cached = runtimeOccupancyRef.current[threadId]
      return typeof cached === 'boolean' ? cached : false
    },
    [backendMode, backendStatus]
  )

  const requireHeadlessRuntimeForAction = useCallback(
    (threadId: string, actionLabel: string): boolean => {
      if (isHeadlessRuntimeClaimed(threadId)) {
        return true
      }
      setSceneActionError(
        threadId,
        `Runtime is released for this conversation. Send a message to claim resources before ${actionLabel.toLowerCase()}.`
      )
      return false
    },
    [isHeadlessRuntimeClaimed, setSceneActionError]
  )

  useEffect(() => {
    if (pendingWelcomePrompt && activeThread && activeThread.messages.length === 0 && !creatingThread) {
      const prompt = pendingWelcomePrompt
      setPendingWelcomePrompt(null)
      void handleSend(prompt)
    }
  }, [pendingWelcomePrompt, activeThread, creatingThread]) // eslint-disable-line react-hooks/exhaustive-deps

  const fetchRenders = useCallback(async (threadId?: string, includeLocalWork: boolean = false) => {
    const targetId = threadId ?? activeThread?.id
    if (!targetId) return
    if (!requireHeadlessRuntimeForAction(targetId, 'fetching renders')) {
      return
    }
    if (rendersAbortRef.current[targetId]) {
      rendersAbortRef.current[targetId].abort()
    }
    const controller = new AbortController()
    rendersAbortRef.current[targetId] = controller
    const timeoutId = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
    setThreadLoading(targetId, { renders: true })
    setSceneActionError(targetId, null)
    try {
      const renders = await getSceneRenders(
        settings.backendUrl,
        targetId,
        includeLocalWork,
        controller.signal
      )
      updateThread(targetId, (thread) => ({ ...thread, renders }))
    } catch (error) {
      console.error('Failed to fetch renders', error)
      setSceneActionError(targetId, formatSceneActionError(error, 'Fetch renders'))
    } finally {
      window.clearTimeout(timeoutId)
      if (rendersAbortRef.current[targetId] === controller) {
        delete rendersAbortRef.current[targetId]
      }
      setThreadLoading(targetId, { renders: false })
    }
  }, [activeThread?.id, requireHeadlessRuntimeForAction, settings.backendUrl, setSceneActionError, setThreadLoading, updateThread])

  const fetchGltf = useCallback(async (threadId?: string) => {
    const targetId = threadId ?? activeThread?.id
    if (!targetId) return
    if (!requireHeadlessRuntimeForAction(targetId, 'fetching scene assets')) {
      return
    }
    if (gltfAbortRef.current[targetId]) {
      gltfAbortRef.current[targetId].abort()
    }
    const controller = new AbortController()
    gltfAbortRef.current[targetId] = controller
    const timeoutId = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
    setThreadLoading(targetId, { gltf: true })
    setSceneActionError(targetId, null)
    try {
      const blob = await getSceneGltf(settings.backendUrl, targetId, controller.signal)
      const nextUrl = URL.createObjectURL(blob)
      updateThread(targetId, (thread) => {
        if (thread.gltfUrl) {
          URL.revokeObjectURL(thread.gltfUrl)
        }
        return { ...thread, gltfUrl: nextUrl }
      })
    } catch (error) {
      console.error('Failed to load GLTF', error)
      setSceneActionError(targetId, formatSceneActionError(error, 'Fetch scene'))
    } finally {
      window.clearTimeout(timeoutId)
      if (gltfAbortRef.current[targetId] === controller) {
        delete gltfAbortRef.current[targetId]
      }
      setThreadLoading(targetId, { gltf: false })
    }
  }, [activeThread?.id, requireHeadlessRuntimeForAction, settings.backendUrl, setSceneActionError, setThreadLoading, updateThread])

  const triggerAutoFetch = useCallback(
    (threadId: string, force: boolean = false) => {
      if (!settingsRef.current.autoRefreshScene || backendStatus !== 'online') return false
      if (!isHeadlessRuntimeClaimed(threadId)) return false
      const currentLoading = loadingRef.current[threadId] ?? createThreadLoadingState()
      if (currentLoading.renders || currentLoading.gltf) return false

      const intervalSeconds = Math.max(1, Math.round(settingsRef.current.autoFetchIntervalSeconds || 10))
      const intervalMs = intervalSeconds * 1000
      const now = Date.now()
      const lastRun = autoFetchLastRunRef.current[threadId] ?? 0
      if (!force && now - lastRun < intervalMs) return false

      autoFetchLastRunRef.current[threadId] = now
      void (async () => {
        await fetchRenders(threadId)
        await fetchGltf(threadId)
      })()
      return true
    },
    [backendStatus, fetchRenders, fetchGltf, isHeadlessRuntimeClaimed]
  )

  const refreshThreadImages = useCallback(
    async (threadId: string) => {
      if (!settings.backendUrl) return
      try {
        const images = await listThreadImages(settings.backendUrl, threadId)
        updateThread(threadId, (thread) => ({
          ...thread,
          images: mergeThreadImages(thread.images ?? [], images)
        }))
      } catch {
        // No-op: thread images are optional
      }
    },
    [settings.backendUrl, updateThread]
  )

  const downloadGltf = useCallback(async () => {
    if (!activeThread) return
    if (!requireHeadlessRuntimeForAction(activeThread.id, 'downloading GLTF')) return
    setThreadLoading(activeThread.id, { download: true })
    setSceneActionError(activeThread.id, null)
    try {
      const blob = await getSceneGltf(settings.backendUrl, activeThread.id)
      const filename = `scene-${activeThread.id}.glb`
      downloadBlob(blob, filename)
    } catch (error) {
      setSceneActionError(activeThread.id, formatSceneActionError(error, 'Download GLTF'))
    } finally {
      setThreadLoading(activeThread.id, { download: false })
    }
  }, [activeThread, requireHeadlessRuntimeForAction, setSceneActionError, setThreadLoading, settings.backendUrl])

  const downloadBlend = useCallback(async () => {
    if (!activeThread) return
    if (!requireHeadlessRuntimeForAction(activeThread.id, 'downloading BLEND')) return
    setThreadLoading(activeThread.id, { download: true })
    setSceneActionError(activeThread.id, null)
    try {
      const blob = await getSceneBlend(settings.backendUrl, activeThread.id)
      const filename = `scene-${activeThread.id}.blend`
      downloadBlob(blob, filename)
    } catch (error) {
      setSceneActionError(activeThread.id, formatSceneActionError(error, 'Download BLEND'))
    } finally {
      setThreadLoading(activeThread.id, { download: false })
    }
  }, [activeThread, requireHeadlessRuntimeForAction, setSceneActionError, setThreadLoading, settings.backendUrl])

  const listBlendFiles = useCallback(async (): Promise<BlendFileEntry[]> => {
    if (!activeThread) return []
    return await listSceneBlendFiles(settings.backendUrl, activeThread.id)
  }, [activeThread, settings.backendUrl])

  const downloadBlendFile = useCallback(
    async (relativePath: string, filename: string) => {
      if (!activeThread) return
      if (!requireHeadlessRuntimeForAction(activeThread.id, 'downloading BLEND')) return
      setThreadLoading(activeThread.id, { download: true })
      setSceneActionError(activeThread.id, null)
      try {
        const blob = await getSceneBlendFile(settings.backendUrl, activeThread.id, relativePath)
        downloadBlob(blob, filename || `scene-${activeThread.id}.blend`)
      } catch (error) {
        setSceneActionError(activeThread.id, formatSceneActionError(error, 'Download BLEND'))
      } finally {
        setThreadLoading(activeThread.id, { download: false })
      }
    },
    [activeThread, requireHeadlessRuntimeForAction, setSceneActionError, setThreadLoading, settings.backendUrl]
  )

  useEffect(() => {
    if (!activeThread) return
    const threadId = activeThread.id
    if (!loadedThreadImagesRef.current.has(threadId)) {
      loadedThreadImagesRef.current.add(threadId)
      void refreshThreadImages(threadId)
    }
  }, [activeThread, activeThread?.id, refreshThreadImages])

  const statusLabel =
    backendStatus === 'online'
      ? 'Online'
      : backendStatus === 'offline'
        ? 'Offline'
        : 'Checking'
  const modeLabel =
    backendMode === 'headless'
      ? 'Headless'
      : backendMode === 'local-client'
        ? 'Local'
        : null
  const canRunSceneActions =
    Boolean(activeThread) &&
    (backendMode !== 'headless' || Boolean(activeThread?.occupyingResources))
  const runtimeClaimHint =
    backendMode === 'headless' && activeThread && !activeThread.occupyingResources
      ? 'Runtime and mcp tools will be claimed when you send the next message'
      : null
  const idleSceneActionHint =
    backendMode === 'headless' && activeThread && !activeThread.occupyingResources
      ? 'Send a message first to claim runtime, then fetch scene/renders.'
      : null
  const quotaHint =
    backendStatus === 'online' && backendMode === 'headless' && headlessQuotaInfo
      ? `Runtime slots in use: ${headlessQuotaInfo.inUse}/${headlessQuotaInfo.quota}`
      : null
  const isMinimalUi = settings.uiMode === 'minimal'
  const statusText = modeLabel ? `Server ${statusLabel} • ${modeLabel}` : `Server ${statusLabel}`
  const projectWebsiteUrl = 'https://3dsceneagent.github.io/vibe3dscene/'
  const projectGithubUrl = 'https://github.com/3DSceneAgent/Vibe3DScene'

  return (
    <div className="app-shell">
      <aside className={`sidebar ${isSidebarCollapsed ? 'collapsed' : ''}`}>
        <div className="sidebar-header">
          {!isSidebarCollapsed && (
            <div className="sidebar-titles">
              <div className="app-title">Vibe 3D Scene</div>
              <div className="app-subtitle">Chat & Scene Console</div>
            </div>
          )}
          <button
            className="ghost-btn icon-btn sidebar-toggle-btn"
            type="button"
            onClick={() => setIsSidebarCollapsed((prev) => !prev)}
            aria-label={isSidebarCollapsed ? 'Expand conversation history' : 'Collapse conversation history'}
            title={isSidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            {isSidebarCollapsed ? '›' : '‹'}
          </button>
        </div>
        {!isSidebarCollapsed && (
          <>
            <ThreadList
              threads={threads}
              activeId={activeThreadId}
              onSelect={(id) => {
                setActiveThreadId(id)
              }}
              onDelete={deleteThread}
              onRename={renameThread}
              onDeleteAll={deleteAllThreads}
              onNew={() => {
                void createThread()
              }}
              onReleaseRuntime={(threadId) => {
                void releaseThreadRuntimeForThread(threadId)
              }}
              creating={creatingThread}
              createDisabled={creatingThread || releasingThreadId !== null}
              releasingThreadId={releasingThreadId}
              createError={threadCreateError}
              createHint={threadCreateHint}
              quotaHint={quotaHint}
            />
            <div className="sidebar-footer">
              <div className="sidebar-status-card" title={`Backend: ${settings.backendUrl}`}>
                <span className={`status-dot ${backendStatus}`} />
                <span className="status-text">{statusText}</span>
              </div>
              <div className="sidebar-shortcuts">
                <button
                  className="ghost-btn sidebar-icon-btn"
                  type="button"
                  onClick={() => setShowSettings(true)}
                  title="Settings"
                  aria-label="Open settings"
                >
                  <svg className="sidebar-icon" viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M12.22 2h-.44a2 2 0 0 0-1.99 1.82l-.2 2.09a7.5 7.5 0 0 0-1.67.96L6 5.74a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l1.8 1.04a7.5 7.5 0 0 0 0 1.92l-1.8 1.04a2 2 0 0 0-.73 2.73l.22.38A2 2 0 0 0 6 18.26l1.92-1.13a7.5 7.5 0 0 0 1.67.96l.2 2.09A2 2 0 0 0 11.78 22h.44a2 2 0 0 0 1.99-1.82l.2-2.09a7.5 7.5 0 0 0 1.67-.96L18 18.26a2 2 0 0 0 2.73-.73l.22-.38a2 2 0 0 0-.73-2.73l-1.8-1.04a7.5 7.5 0 0 0 0-1.92l1.8-1.04a2 2 0 0 0 .73-2.73l-.22-.38A2 2 0 0 0 18 5.74l-1.92 1.13a7.5 7.5 0 0 0-1.67-.96l-.2-2.09A2 2 0 0 0 12.22 2z" />
                    <circle cx="12" cy="12" r="2.7" />
                  </svg>
                </button>
                <a
                  className="ghost-btn sidebar-icon-btn"
                  href={projectWebsiteUrl}
                  target="_blank"
                  rel="noreferrer"
                  title="Website"
                  aria-label="Open project website"
                >
                  <svg className="sidebar-icon" viewBox="0 0 24 24" aria-hidden="true">
                    <circle cx="12" cy="12" r="8.5" />
                    <path d="M3.5 12h17M12 3.5c2.3 2.1 3.5 5.3 3.5 8.5S14.3 18.4 12 20.5c-2.3-2.1-3.5-5.3-3.5-8.5S9.7 5.6 12 3.5" />
                  </svg>
                </a>
                <a
                  className="ghost-btn sidebar-icon-btn"
                  href={projectGithubUrl}
                  target="_blank"
                  rel="noreferrer"
                  title="GitHub"
                  aria-label="Open project GitHub"
                >
                  <svg className="sidebar-icon" viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M9 19c-4.7 1.4-4.7-2.2-6.6-2.6M15 21v-3.1a2.7 2.7 0 0 0-.8-2.1c2.8-.3 5.8-1.4 5.8-6.2a4.8 4.8 0 0 0-1.3-3.3a4.5 4.5 0 0 0-.1-3.2s-1.1-.3-3.6 1.3a12.5 12.5 0 0 0-6 0C6.5 2.8 5.4 3.1 5.4 3.1a4.5 4.5 0 0 0-.1 3.2A4.8 4.8 0 0 0 4 9.6c0 4.8 2.9 5.9 5.8 6.2a2.6 2.6 0 0 0-.8 2.1V21" />
                  </svg>
                </a>
              </div>
            </div>
          </>
        )}
      </aside>

      <main className="main">
        {showSettings && (
          <div className="settings-overlay">
            <div className="settings-card">
              <div className="settings-header">
                <div className="panel-title">Settings</div>
                <button className="text-btn" onClick={() => setShowSettings(false)}>
                  Close
                </button>
              </div>
              <SettingsPanel settings={settings} onChange={setSettings} />
            </div>
          </div>
        )}

        <div className={`workspace ${activeThread ? '' : 'is-empty'} ${isMinimalUi ? 'minimal-ui' : ''}`}>
          {activeThread ? (
            <>
              <section className="workspace-chat">
                <ChatTab
                  thread={activeThread}
                  isStreaming={isStreaming || isSending}
                  streamStatus={
                    streamStatusByThread[activeThread.id] ??
                    (isStreaming && currentStreamRef.current?.threadId === activeThread.id ? 'streaming' : 'complete')
                  }
                  onSend={handleSend}
                  onStop={isStreaming ? handleStop : undefined}
                  backendUrl={settings.backendUrl}
                  examplePrompts={examplePrompts}
                  mcpTools={mcpToolsByThread[activeThread.id] ?? []}
                  mcpToolHints={mcpToolHintsByThread[activeThread.id] ?? {}}
                  mcpToolEnabled={activeThread.mcpToolEnabled ?? {}}
                  onMcpToolToggle={handleMcpToolToggle}
                  mcpToolsLoading={mcpToolsLoadingThreadId === activeThread.id}
                  mcpToolsError={mcpToolsErrorByThread[activeThread.id] ?? null}
                  vlmProviders={vlmProviders}
                  vlmProvider={activeThread.vlmProvider ?? vlmDefaultProvider}
                  vlmModel={activeThread.vlmModel ?? vlmDefaultModel}
                  fastMode={activeThreadFastMode}
                  fastModeAvailable={fastModeAvailable}
                  vlmLoading={vlmLoadingThreadId === activeThread.id}
                  vlmError={vlmErrorByThread[activeThread.id] ?? null}
                  vlmLocked={Boolean(activeThread.vlmLocked)}
                  onVlmSelectionChange={handleVlmSelectionChange}
                  onFastModeToggle={handleFastModeToggle}
                  graphEvents={activeThread.graphEvents ?? []}
                  todos={activeThread.todos ?? []}
                  runtimeClaimHint={runtimeClaimHint}
                  minimalUi={isMinimalUi}
                />
              </section>
              <section className="workspace-scene">
                <SceneTab
                  threadId={activeThread.id}
                  renders={activeThread.renders ?? []}
                  backendUrl={settings.backendUrl}
                  gltfUrl={activeThread.gltfUrl ?? null}
                  sceneHierarchy={activeThread.sceneHierarchy ?? []}
                  environment={environment}
                  viewportTheme={settings.viewportTheme}
                  uiTheme={settings.theme}
                  onEnvironmentChange={setEnvironment}
                  onFetchRenders={(includeLocalWork) => void fetchRenders(undefined, includeLocalWork ?? false)}
                  onFetchGltf={() => void fetchGltf()}
                  onDownloadGltf={() => void downloadGltf()}
                  onDownloadBlend={() => void downloadBlend()}
                  onDownloadBlendFile={(relativePath, filename) => void downloadBlendFile(relativePath, filename)}
                  onListBlendFiles={() => listBlendFiles()}
                  actionError={activeSceneActionError}
                  onClearActionError={() => setSceneActionError(activeThread.id, null)}
                  onHierarchyChange={handleSceneHierarchyChange}
                  loading={activeThreadLoading}
                  canRunActions={canRunSceneActions}
                  idleActionHint={idleSceneActionHint}
                  minimalUi={isMinimalUi}
                />
              </section>
            </>
          ) : (
            <section className="workspace-empty">
              <div className="workspace-empty-card">
                <div className="welcome-hero">
                  <img src="/demo.gif" alt="3D scene creation demo" loading="lazy" />
                </div>
                <div className="workspace-empty-badge">
                  <span className="sparkle" aria-hidden="true">&#10022;</span>{' '}AI-Powered 3D Creation
                </div>
                <div className="workspace-empty-title">Bring Your 3D Vision to Life</div>
                <div className="workspace-empty-subtitle">
                  Describe what you imagine — the AI scene agent will build, refine, and render your 3D scene in real time.
                </div>

                {examplePrompts.length > 0 && (
                  <div className="welcome-prompt-grid">
                    {examplePrompts.slice(0, 4).map((prompt, index) => (
                      <button
                        key={`${index}-${prompt}`}
                        type="button"
                        className="welcome-prompt-card"
                        disabled={creatingThread || releasingThreadId !== null}
                        onClick={() => {
                          setPendingWelcomePrompt(prompt)
                          void createThread()
                        }}
                        title={prompt}
                      >
                        <span className="welcome-prompt-icon" aria-hidden="true">
                          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M3 13l3-3m0 0l7-7m-7 7l-3-3m10 3l-7 7" />
                          </svg>
                        </span>
                        <span className="welcome-prompt-text">{prompt}</span>
                      </button>
                    ))}
                  </div>
                )}

                <button
                  className="primary-btn workspace-empty-cta"
                  onClick={() => {
                    void createThread()
                  }}
                  disabled={creatingThread || releasingThreadId !== null}
                >
                  {releasingThreadId ? 'Releasing...' : creatingThread ? 'Creating...' : 'Start Creating'}
                </button>
                <div className="workspace-empty-features">
                  <span className="workspace-empty-feature">
                    <span className="workspace-empty-feature-icon" aria-hidden="true">&#128172;</span>
                    Chat-Driven
                  </span>
                  <span className="workspace-empty-feature">
                    <span className="workspace-empty-feature-icon" aria-hidden="true">&#9655;</span>
                    Real-Time 3D
                  </span>
                  <span className="workspace-empty-feature">
                    <span className="workspace-empty-feature-icon" aria-hidden="true">&#128247;</span>
                    Render &amp; Export
                  </span>
                </div>
                {threadCreateHint && <div className="thread-create-hint action">{threadCreateHint}</div>}
                {threadCreateError && <div className="thread-create-error">{threadCreateError}</div>}
              </div>
            </section>
          )}
        </div>
      </main>
    </div>
  )
}

export default App
