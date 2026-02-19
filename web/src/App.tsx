import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  streamChat,
  getScene,
  getSceneRenders,
  getSceneGltf,
  getSceneBlend,
  listSceneBlendFiles,
  getSceneBlendFile,
  deleteThread as deleteThreadApi,
  getHealth,
  uploadReferenceImages,
  listReferenceImages,
  getExamplePrompts,
  getMcpTools,
  getVlmModels
} from './api/client'
import type {
  BlendFileEntry,
  GraphNodeStream,
  ReferenceImage,
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
  extractMessageContent,
  extractToolPayload,
  isHumanMessage,
  isToolMessage,
  parseThinking
} from './utils/message'
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

function App() {
  const REQUEST_TIMEOUT_MS = 35000
  const MCP_REQUEST_TIMEOUT_MS = 10000
  const VLM_REQUEST_TIMEOUT_MS = 10000
  const [threads, setThreads] = useState<Thread[]>(() => loadThreads())
  const [activeThreadId, setActiveThreadId] = useState<string | null>(() => loadThreads()[0]?.id ?? null)
  const [settings, setSettings] = useState(() => loadSettings())
  const [environment, setEnvironment] = useState<'studio' | 'warm' | 'cool'>('studio')
  const [isStreaming, setIsStreaming] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false)
  const [backendStatus, setBackendStatus] = useState<'online' | 'offline' | 'checking'>('checking')
  const [backendMode, setBackendMode] = useState<'headless' | 'local-client' | null>(null)
  const [examplePrompts, setExamplePrompts] = useState<string[]>([])
  const [mcpToolsByThread, setMcpToolsByThread] = useState<Record<string, string[]>>({})
  const [mcpToolHintsByThread, setMcpToolHintsByThread] = useState<Record<string, Record<string, string>>>({})
  const [mcpToolsErrorByThread, setMcpToolsErrorByThread] = useState<Record<string, string | null>>({})
  const [mcpToolsLoadingThreadId, setMcpToolsLoadingThreadId] = useState<string | null>(null)
  const [vlmProviders, setVlmProviders] = useState<VlmProviderOption[]>([])
  const [vlmDefaultProvider, setVlmDefaultProvider] = useState<string>('openai')
  const [vlmDefaultModel, setVlmDefaultModel] = useState<string>('')
  const [vlmErrorByThread, setVlmErrorByThread] = useState<Record<string, string | null>>({})
  const [vlmLoadingThreadId, setVlmLoadingThreadId] = useState<string | null>(null)
  const [loadingByThread, setLoadingByThread] = useState<Record<string, ThreadLoadingState>>({})
  const [sceneActionErrorByThread, setSceneActionErrorByThread] = useState<Record<string, string | null>>({})
  const [streamStatusByThread, setStreamStatusByThread] = useState<Record<string, 'streaming' | 'complete'>>({})
  const streamAbortRef = useRef<AbortController | null>(null)
  const healthAbortRef = useRef<AbortController | null>(null)
  const sceneAbortRef = useRef<Record<string, AbortController>>({})
  const rendersAbortRef = useRef<Record<string, AbortController>>({})
  const gltfAbortRef = useRef<Record<string, AbortController>>({})
  const requestedSceneRef = useRef<Set<string>>(new Set())
  const previousAssistantContentRef = useRef<string | null>(null)
  const knownStreamIdsRef = useRef<Set<string>>(new Set())
  const knownToolIdsRef = useRef<Set<string>>(new Set())
  const receivedDeltaRef = useRef(false)
  const sceneChangeRef = useRef<Record<string, boolean>>({})
  const settingsRef = useRef(settings)
  const loadedReferenceImagesRef = useRef<Set<string>>(new Set())
  const currentStreamRef = useRef<{ threadId: string; assistantId: string; runId: number } | null>(null)
  const streamRunIdRef = useRef(0)
  const messageIdMapRef = useRef<Map<string, string>>(new Map())
  const saveThreadsTimerRef = useRef<number | null>(null)
  const loadingRef = useRef<Record<string, ThreadLoadingState>>({})
  const initialAutoFetchRef = useRef<Set<string>>(new Set())
  const autoFetchLastRunRef = useRef<Record<string, number>>({})

  const activeThread = useMemo(
    () => threads.find((thread) => thread.id === activeThreadId) ?? null,
    [threads, activeThreadId]
  )
  const activeThreadLoading = useMemo(
    () => (activeThread ? loadingByThread[activeThread.id] ?? createThreadLoadingState() : createThreadLoadingState()),
    [activeThread, loadingByThread]
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

  // Load data from IndexedDB on mount
  useEffect(() => {
    let mounted = true
    const loadData = async () => {
      const [loadedThreads, loadedSettings] = await Promise.all([
        loadThreadsAsync(),
        loadSettingsAsync()
      ])
      if (mounted) {
        if (loadedThreads.length > 0) {
          setThreads(loadedThreads)
          setActiveThreadId((current) => current ?? loadedThreads[0]?.id ?? null)
        }
        setSettings(loadedSettings)
      }
    }
    loadData()
    return () => {
      mounted = false
    }
  }, [])

  useEffect(() => {
    if (!activeThreadId && threads.length > 0) {
      setActiveThreadId(threads[0].id)
    }
  }, [threads, activeThreadId])

  useEffect(() => {
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
  }, [threads])

  useEffect(() => {
    saveSettings(settings)
    document.documentElement.dataset.theme = settings.theme
    settingsRef.current = settings
  }, [settings])

  useEffect(() => {
    loadingRef.current = loadingByThread
  }, [loadingByThread])

  useEffect(() => {
    let isActive = true

    const checkHealth = async () => {
      if (!settings.backendUrl) {
        if (isActive) {
          setBackendStatus('offline')
          setBackendMode(null)
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
        if (isActive) {
          setBackendStatus('online')
          setBackendMode(health.blender_mode ?? null)
        }
      } catch {
        if (isActive) {
          setBackendStatus('offline')
          setBackendMode(null)
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
    const fetchPrompts = async () => {
      try {
        const prompts = await getExamplePrompts(settings.backendUrl)
        if (!cancelled) {
          setExamplePrompts(prompts)
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
  }, [settings.backendUrl])

  useEffect(() => {
    let cancelled = false
    const threadId = activeThread?.id
    if (!threadId || !settings.backendUrl || backendStatus !== 'online') {
      return () => {
        cancelled = true
      }
    }

    const controller = new AbortController()
    const timeoutId = window.setTimeout(() => controller.abort(), MCP_REQUEST_TIMEOUT_MS)
    setMcpToolsLoadingThreadId(threadId)
    const fetchTools = async () => {
      try {
        const toolInfo = await getMcpTools(settings.backendUrl, threadId, controller.signal)
        if (cancelled) return
        setMcpToolsByThread((prev) => ({ ...prev, [threadId]: toolInfo.tools }))
        setMcpToolHintsByThread((prev) => ({ ...prev, [threadId]: toolInfo.tool_hints }))
        setMcpToolsErrorByThread((prev) => ({ ...prev, [threadId]: null }))
      } catch (error) {
        if (cancelled) return
        setMcpToolsByThread((prev) => ({ ...prev, [threadId]: [] }))
        setMcpToolHintsByThread((prev) => ({ ...prev, [threadId]: {} }))
        setMcpToolsErrorByThread((prev) => ({
          ...prev,
          [threadId]:
            error instanceof DOMException && error.name === 'AbortError'
              ? 'Timed out while loading MCP tools'
              : error instanceof Error
                ? error.message
                : 'Failed to load MCP tools'
        }))
      } finally {
        window.clearTimeout(timeoutId)
        if (!cancelled) {
          setMcpToolsLoadingThreadId((current) => (current === threadId ? null : current))
        }
      }
    }
    void fetchTools()
    return () => {
      cancelled = true
      controller.abort()
      window.clearTimeout(timeoutId)
    }
  }, [activeThread?.id, settings.backendUrl, backendStatus, MCP_REQUEST_TIMEOUT_MS])

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
          const preferredProvider = thread.vlmProvider || modelInfo.thread_selection?.provider || fallbackProvider
          const selectedProvider =
            modelInfo.providers.find((item) => item.provider === preferredProvider)?.provider || fallbackProvider
          const providerOption = modelInfo.providers.find((item) => item.provider === selectedProvider)
          const providerModels = providerOption?.models ?? []
          let nextModel =
            thread.vlmModel ||
            modelInfo.thread_selection?.model ||
            providerOption?.default_model ||
            modelInfo.default_model
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

  const createThread = () => {
    const initialProvider = vlmDefaultProvider || vlmProviders[0]?.provider
    const initialProviderOption = vlmProviders.find((item) => item.provider === initialProvider)
    const initialModel = initialProviderOption?.default_model || vlmDefaultModel
    const newThread: Thread = {
      id: `thread-${crypto.randomUUID()}`,
      title: 'New chat',
      createdAt: Date.now(),
      messages: [],
      mcpToolEnabled: {},
      vlmProvider: initialProvider,
      vlmModel: initialModel,
      vlmLocked: false,
      todos: [],
      scene: null,
      renders: [],
      gltfUrl: null,
      sceneHierarchy: [],
      sceneHasChange: false,
      referenceImages: [],
      graphEvents: []
    }
    setThreads((prev) => [newThread, ...prev])
    setActiveThreadId(newThread.id)
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
      if (target?.referenceImages) {
        target.referenceImages.forEach((image) => {
          if (image.previewUrl) {
            URL.revokeObjectURL(image.previewUrl)
          }
        })
      }
      return prev.filter((thread) => thread.id !== threadId)
    })
    requestedSceneRef.current.delete(threadId)
    loadedReferenceImagesRef.current.delete(threadId)
    initialAutoFetchRef.current.delete(threadId)
    delete autoFetchLastRunRef.current[threadId]
    delete sceneChangeRef.current[threadId]
    if (sceneAbortRef.current[threadId]) {
      sceneAbortRef.current[threadId].abort()
      delete sceneAbortRef.current[threadId]
    }
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
    if (activeThreadId === threadId) {
      const remaining = threads.filter((thread) => thread.id !== threadId)
      setActiveThreadId(remaining[0]?.id ?? null)
    }
  }

  const mergeTodos = (existing: TodoItem[], incoming: TodoItem[]) => {
    const map = new Map(existing.map((todo) => [todo.id, todo]))
    incoming.forEach((todo) => map.set(todo.id, todo))
    return Array.from(map.values())
  }

  const mergeReferenceImages = (
    existing: ReferenceImage[],
    incoming: ReferenceImage[]
  ): ReferenceImage[] => {
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

  const handleSceneHierarchyChange = useCallback(
    (threadId: string, hierarchy: SceneHierarchyNode[]) => {
      updateThread(threadId, (thread) => ({
        ...thread,
        sceneHierarchy: hierarchy
      }))
    },
    [updateThread]
  )

  const handleStop = useCallback(() => {
    if (streamAbortRef.current) {
      streamAbortRef.current.abort()
    }
    
    if (currentStreamRef.current) {
      const { threadId, assistantId, runId } = currentStreamRef.current
      updateThread(threadId, (thread) => ({
        ...thread,
        messages: thread.messages.map((message) =>
          message.id === assistantId && message.status === 'streaming'
            ? { ...message, status: 'final' }
            : message
        )
      }))
      setThreadStreamStatus(threadId, 'complete')
      if (streamRunIdRef.current === runId) {
        streamRunIdRef.current += 1
      }
      currentStreamRef.current = null
    }
    
    messageIdMapRef.current.clear()
    setIsStreaming(false)
  }, [setThreadStreamStatus, updateThread])

  const handleSend = async (text: string, files: File[] = []) => {
    if (!activeThread) {
      return false
    }
    const threadId = activeThread.id
    const hasMcpToolSnapshot =
      Object.prototype.hasOwnProperty.call(mcpToolsByThread, threadId) &&
      !mcpToolsErrorByThread[threadId]
    const availableMcpTools = mcpToolsByThread[threadId] ?? []
    const enabledMcpTools = hasMcpToolSnapshot
      ? availableMcpTools.filter((toolName) => activeThread.mcpToolEnabled?.[toolName] !== false)
      : undefined
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

    if (files.length > 0) {
      if (!settings.backendUrl) {
        return false
      }
      try {
        const uploaded = await uploadReferenceImages(settings.backendUrl, threadId, files)
        const nextImages = uploaded.map((image, index) => ({
          ...image,
          previewUrl: files[index] ? URL.createObjectURL(files[index]) : undefined
        }))
        updateThread(threadId, (thread) => ({
          ...thread,
          referenceImages: mergeReferenceImages(thread.referenceImages ?? [], nextImages)
        }))
      } catch (error) {
        updateThread(threadId, (thread) => ({
          ...thread,
          messages: [
            ...thread.messages,
            {
              id: `msg-${Date.now()}-upload-error`,
              role: 'assistant',
              content: `Error: ${(error as Error).message}`,
              createdAt: Date.now(),
              status: 'error'
            }
          ]
        }))
        return false
      }
    }

    if (streamAbortRef.current) {
      streamAbortRef.current.abort()
    }
    if (currentStreamRef.current) {
      const { threadId: previousThreadId, assistantId: previousAssistantId } = currentStreamRef.current
      updateThread(previousThreadId, (thread) => ({
        ...thread,
        messages: thread.messages.map((message) =>
          message.id === previousAssistantId && message.status === 'streaming'
            ? { ...message, status: 'final' }
            : message
        )
      }))
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
    receivedDeltaRef.current = false

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
      createdAt: now,
      streamId: null,
      status: 'streaming'
    }

    updateThread(threadId, (thread) => {
      const title =
        thread.title === 'New chat' || thread.messages.length === 0
          ? text.slice(0, 32)
          : thread.title
      return {
        ...thread,
        title,
        vlmProvider: selectedProvider || thread.vlmProvider,
        vlmModel: selectedModel || thread.vlmModel,
        vlmLocked: thread.vlmLocked ?? false,
        graphEvents: [],
        messages: [...thread.messages, userMessage, assistantMessage]
      }
    })

    setIsStreaming(true)
    setThreadStreamStatus(threadId, 'streaming')
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
            ? { ...message, status: 'final' as const }
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
          createdAt: Date.now(),
          streamId: messageId,
          status: 'streaming'
        }
        updateThread(threadId, (thread) => ({
          ...thread,
          messages: [...thread.messages, newMessage]
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
            thinking: next.thinking,
            raw: next.raw,
            streamId: next.messageId,
            status: 'streaming'
          }
        })
        return
      }

      if (event.messages && event.messages.length > 0) {
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
            messages: [...thread.messages, ...toolEntries]
          }))
        }

        const candidates = event.messages.filter(
          (message) => !isHumanMessage(message) && !isToolMessage(message)
        )
        if (candidates.length === 0) return
        let selected = candidates[candidates.length - 1]
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
        const raw = extractMessageContent(selected)
        if (!raw) return
        const parsed = parseThinking(raw)
        const streamId =
          typeof selected === 'object' && selected !== null && 'id' in selected
            ? (selected as { id?: string | null }).id ?? null
            : null
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
          thinking: parsed.thinking,
          raw,
          streamId,
          status: 'streaming'
        }))
      }
    }

    const streamPromise = streamChat({
      baseUrl: settings.backendUrl,
      message: text,
      threadId,
      enabledMcpTools,
      vlmProvider: selectedProvider,
      vlmModel: selectedModel,
      signal: abortController.signal,
      onEvent: handleStreamEvent
    })
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
              ? { ...message, status: 'final' }
              : message
            )
        }))
        setThreadStreamStatus(threadId, 'complete')
        setIsStreaming(false)
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

  const refreshScene = useCallback(async (threadId?: string) => {
    const targetId = threadId ?? activeThread?.id
    if (!targetId) return
    if (sceneAbortRef.current[targetId]) {
      sceneAbortRef.current[targetId].abort()
    }
    const controller = new AbortController()
    sceneAbortRef.current[targetId] = controller
    const timeoutId = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
    setThreadLoading(targetId, { scene: true })
    setSceneActionError(targetId, null)
    try {
      const scene = await getScene(settings.backendUrl, targetId, controller.signal)
      updateThread(targetId, (thread) => ({ ...thread, scene }))
    } catch (error) {
      console.error('Failed to refresh scene', error)
      setSceneActionError(targetId, formatSceneActionError(error, 'Fetch scene'))
    } finally {
      window.clearTimeout(timeoutId)
      if (sceneAbortRef.current[targetId] === controller) {
        delete sceneAbortRef.current[targetId]
      }
      setThreadLoading(targetId, { scene: false })
    }
  }, [activeThread?.id, settings.backendUrl, setSceneActionError, setThreadLoading, updateThread])

  const fetchRenders = useCallback(async (threadId?: string, includeLocalWork: boolean = false) => {
    const targetId = threadId ?? activeThread?.id
    if (!targetId) return
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
  }, [activeThread?.id, settings.backendUrl, setSceneActionError, setThreadLoading, updateThread])

  const fetchGltf = useCallback(async (threadId?: string) => {
    const targetId = threadId ?? activeThread?.id
    if (!targetId) return
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
  }, [activeThread?.id, settings.backendUrl, setSceneActionError, setThreadLoading, updateThread])

  const triggerAutoFetch = useCallback(
    (threadId: string, force: boolean = false) => {
      if (!settingsRef.current.autoRefreshScene || backendStatus !== 'online') return false
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
    [backendStatus, fetchRenders, fetchGltf]
  )

  const refreshReferenceImages = useCallback(
    async (threadId: string) => {
      if (!settings.backendUrl) return
      try {
        const images = await listReferenceImages(settings.backendUrl, threadId)
        updateThread(threadId, (thread) => ({
          ...thread,
          referenceImages: mergeReferenceImages(thread.referenceImages ?? [], images)
        }))
      } catch {
        // No-op: reference images are optional
      }
    },
    [settings.backendUrl, updateThread]
  )

  const downloadGltf = useCallback(async () => {
    if (!activeThread) return
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
  }, [activeThread, setSceneActionError, setThreadLoading, settings.backendUrl])

  const downloadBlend = useCallback(async () => {
    if (!activeThread) return
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
  }, [activeThread, setSceneActionError, setThreadLoading, settings.backendUrl])

  const listBlendFiles = useCallback(async (): Promise<BlendFileEntry[]> => {
    if (!activeThread) return []
    return await listSceneBlendFiles(settings.backendUrl, activeThread.id)
  }, [activeThread, settings.backendUrl])

  const downloadBlendFile = useCallback(
    async (relativePath: string, filename: string) => {
      if (!activeThread) return
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
    [activeThread, setSceneActionError, setThreadLoading, settings.backendUrl]
  )

  useEffect(() => {
    if (!activeThread) return
    const threadId = activeThread.id
    if (!requestedSceneRef.current.has(threadId) && !activeThreadLoading.scene && !activeThread.scene) {
      requestedSceneRef.current.add(threadId)
      refreshScene(threadId)
    }
  }, [
    activeThread,
    activeThread?.id,
    activeThread?.scene,
    activeThreadLoading.scene,
    refreshScene
  ])

  useEffect(() => {
    const threadId = activeThread?.id
    if (!threadId) return
    if (!settings.autoRefreshScene || backendStatus !== 'online' || !settings.backendUrl) return
    if (initialAutoFetchRef.current.has(threadId)) return
    if (activeThread?.gltfUrl || (activeThread?.renders?.length ?? 0) > 0) {
      initialAutoFetchRef.current.add(threadId)
      return
    }
    const started = triggerAutoFetch(threadId, true)
    if (started) {
      initialAutoFetchRef.current.add(threadId)
    }
  }, [
    activeThread?.id,
    activeThread?.gltfUrl,
    activeThread?.renders,
    settings.autoRefreshScene,
    settings.autoFetchIntervalSeconds,
    settings.backendUrl,
    backendStatus,
    activeThreadLoading.renders,
    activeThreadLoading.gltf,
    triggerAutoFetch
  ])

  useEffect(() => {
    if (!activeThread) return
    const threadId = activeThread.id
    if (!loadedReferenceImagesRef.current.has(threadId)) {
      loadedReferenceImagesRef.current.add(threadId)
      void refreshReferenceImages(threadId)
    }
  }, [activeThread, activeThread?.id, refreshReferenceImages])

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
  const statusText = modeLabel ? `Server ${statusLabel} • ${modeLabel}` : `Server ${statusLabel}`
  const projectWebsiteUrl = 'https://github.com/3DSceneAgent/3DSceneAgent#readme'
  const projectGithubUrl = 'https://github.com/3DSceneAgent/3DSceneAgent'

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
            className="ghost-btn icon-btn"
            onClick={() => setIsSidebarCollapsed((prev) => !prev)}
            aria-label={isSidebarCollapsed ? 'Expand conversation history' : 'Collapse conversation history'}
          >
            {isSidebarCollapsed ? '›' : '‹'}
          </button>
        </div>
        <ThreadList
          threads={threads}
          activeId={activeThreadId}
          onSelect={(id) => {
            setActiveThreadId(id)
          }}
          onDelete={deleteThread}
          onNew={createThread}
          collapsed={isSidebarCollapsed}
        />
        <div className="sidebar-footer">
          <div className={`sidebar-status-card ${isSidebarCollapsed ? 'compact' : ''}`} title={`Backend: ${settings.backendUrl}`}>
            <span className={`status-dot ${backendStatus}`} />
            {!isSidebarCollapsed && <span className="status-text">{statusText}</span>}
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

        <div className={`workspace ${activeThread ? '' : 'is-empty'}`}>
          {activeThread ? (
            <>
              <section className="workspace-chat">
                <ChatTab
                  thread={activeThread}
                  isStreaming={isStreaming}
                  streamStatus={
                    streamStatusByThread[activeThread.id] ??
                    (isStreaming && currentStreamRef.current?.threadId === activeThread.id ? 'streaming' : 'complete')
                  }
                  onSend={handleSend}
                  onStop={handleStop}
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
                  vlmLoading={vlmLoadingThreadId === activeThread.id}
                  vlmError={vlmErrorByThread[activeThread.id] ?? null}
                  vlmLocked={Boolean(activeThread.vlmLocked)}
                  onVlmSelectionChange={handleVlmSelectionChange}
                  graphEvents={activeThread.graphEvents ?? []}
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
                  autoFetch={settings.autoRefreshScene}
                  onAutoFetchChange={(enabled) =>
                    setSettings((prev) => ({ ...prev, autoRefreshScene: enabled }))
                  }
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
                  canRunActions={Boolean(activeThread)}
                />
              </section>
            </>
          ) : (
            <section className="workspace-empty">
              <div className="workspace-empty-card">
                <div className="workspace-empty-badge">Vibe 3D Scene</div>
                <div className="workspace-empty-title">Start Vibe Building 3D Scene</div>
                <div className="workspace-empty-subtitle">
                </div>
                <button className="primary-btn workspace-empty-cta" onClick={createThread}>
                  New Chat
                </button>
              </div>
            </section>
          )}
        </div>
      </main>
    </div>
  )
}

export default App
