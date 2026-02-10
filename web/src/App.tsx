import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  streamChat,
  getScene,
  getSceneRenders,
  getSceneGltf,
  getSceneBlend,
  getHealth,
  uploadReferenceImages,
  listReferenceImages
} from './api/client'
import type { ReferenceImage, StreamEvent, TodoItem } from './api/types'
import { ChatTab } from './components/ChatTab'
import { SceneTab } from './components/SceneTab'
import { SettingsPanel } from './components/SettingsPanel'
import { TopBar } from './components/TopBar'
import { ThreadList } from './components/ThreadList'
import { loadSettings, loadThreads, loadSettingsAsync, loadThreadsAsync, saveSettings, saveThreads } from './state/storage'
import type { Message, Thread } from './state/types'
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

function App() {
  const REQUEST_TIMEOUT_MS = 35000
  const [threads, setThreads] = useState<Thread[]>(() => loadThreads())
  const [activeThreadId, setActiveThreadId] = useState<string | null>(() => loadThreads()[0]?.id ?? null)
  const [settings, setSettings] = useState(() => loadSettings())
  const [environment, setEnvironment] = useState<'studio' | 'warm' | 'cool'>('studio')
  const [isStreaming, setIsStreaming] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false)
  const [backendStatus, setBackendStatus] = useState<'online' | 'offline' | 'checking'>('checking')
  const [backendMode, setBackendMode] = useState<'headless' | 'local-client' | null>(null)
  const [loading, setLoading] = useState({
    scene: false,
    renders: false,
    gltf: false,
    download: false
  })
  const streamAbortRef = useRef<AbortController | null>(null)
  const healthAbortRef = useRef<AbortController | null>(null)
  const sceneAbortRef = useRef<AbortController | null>(null)
  const rendersAbortRef = useRef<AbortController | null>(null)
  const gltfAbortRef = useRef<AbortController | null>(null)
  const requestedSceneRef = useRef<Set<string>>(new Set())
  const previousAssistantContentRef = useRef<string | null>(null)
  const knownStreamIdsRef = useRef<Set<string>>(new Set())
  const knownToolIdsRef = useRef<Set<string>>(new Set())
  const receivedDeltaRef = useRef(false)
  const sceneChangeRef = useRef<Record<string, boolean>>({})
  const settingsRef = useRef(settings)
  const loadedReferenceImagesRef = useRef<Set<string>>(new Set())
  const currentStreamRef = useRef<{ threadId: string; assistantId: string } | null>(null)
  const messageIdMapRef = useRef<Map<string, string>>(new Map())

  const activeThread = useMemo(
    () => threads.find((thread) => thread.id === activeThreadId) ?? null,
    [threads, activeThreadId]
  )

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
          if (!activeThreadId) {
            setActiveThreadId(loadedThreads[0].id)
          }
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
    saveThreads(threads)
  }, [threads])

  useEffect(() => {
    saveSettings(settings)
    document.documentElement.dataset.theme = settings.theme
    settingsRef.current = settings
  }, [settings])

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

  const updateThread = useCallback((threadId: string, updater: (thread: Thread) => Thread) => {
    setThreads((prev) => prev.map((thread) => (thread.id === threadId ? updater(thread) : thread)))
  }, [])

  const createThread = () => {
    const newThread: Thread = {
      id: `thread-${Date.now()}`,
      title: 'New chat',
      createdAt: Date.now(),
      messages: [],
      todos: [],
      scene: null,
      renders: [],
      gltfUrl: null,
      sceneHasChange: false,
      referenceImages: []
    }
    setThreads((prev) => [newThread, ...prev])
    setActiveThreadId(newThread.id)
  }

  const deleteThread = (threadId: string) => {
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
    delete sceneChangeRef.current[threadId]
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

  const toggleSceneTab = useCallback(() => {
    setSettings((prev) => ({ ...prev, sceneTabCollapsed: !prev.sceneTabCollapsed }))
  }, [])

  const handleStop = useCallback(() => {
    if (streamAbortRef.current) {
      streamAbortRef.current.abort()
    }
    
    if (currentStreamRef.current) {
      const { threadId, assistantId } = currentStreamRef.current
      updateThread(threadId, (thread) => ({
        ...thread,
        messages: thread.messages.map((message) =>
          message.id === assistantId && message.status === 'streaming'
            ? { ...message, status: 'final' }
            : message
        )
      }))
      currentStreamRef.current = null
    }
    
    setIsStreaming(false)
  }, [updateThread])

  const handleSend = async (text: string, files: File[] = []) => {
    if (!activeThread) {
      return false
    }
    const threadId = activeThread.id

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
        messages: [...thread.messages, userMessage, assistantMessage]
      }
    })

    setIsStreaming(true)
    const abortController = new AbortController()
    streamAbortRef.current = abortController
    currentStreamRef.current = { threadId, assistantId }
    messageIdMapRef.current.clear()
    messageIdMapRef.current.set('initial', assistantId)
    const handleStreamEvent = (event: StreamEvent) => {
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
        updateAssistantById(assistantId, (message) => ({ ...message, content: `Error: ${event.error}`, status: 'error' }))
        return
      }

      if (event.todos && event.todos.length > 0) {
        updateThread(threadId, (thread) => ({
          ...thread,
          todos: mergeTodos(thread.todos, event.todos || [])
        }))
      }

      if (typeof event.scene_has_change === 'boolean') {
        const nextSceneChange = updateSceneChange(event.scene_has_change, event.event === 'done')
        if (event.event === 'done') {
          if (settingsRef.current.autoRefreshScene && nextSceneChange) {
            void fetchRenders(threadId)
            void fetchGltf(threadId)
          }
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
      signal: abortController.signal,
      onEvent: handleStreamEvent
    })
    streamPromise
      .catch((error) => {
        updateThread(threadId, (thread) => {
          let lastAssistantIndex = -1
          for (let i = thread.messages.length - 1; i >= 0; i -= 1) {
            const message = thread.messages[i]
            if (message.role === 'assistant' && message.status === 'streaming') {
              lastAssistantIndex = i
              break
            }
          }
          if (lastAssistantIndex === -1) {
            return thread
          }
          return {
            ...thread,
            messages: thread.messages.map((message, index) =>
              index === lastAssistantIndex
                ? { ...message, content: `Error: ${String(error)}`, status: 'error' }
                : message
            )
          }
        })
      })
      .finally(() => {
        updateThread(threadId, (thread) => ({
          ...thread,
          messages: thread.messages.map((message) =>
            message.status === 'streaming'
              ? { ...message, status: 'final' }
              : message
          )
        }))
        setIsStreaming(false)
        currentStreamRef.current = null
        messageIdMapRef.current.clear()
      })
    return true
  }

  const refreshScene = useCallback(async (threadId?: string) => {
    const targetId = threadId ?? activeThread?.id
    if (!targetId) return
    if (sceneAbortRef.current) {
      sceneAbortRef.current.abort()
    }
    const controller = new AbortController()
    sceneAbortRef.current = controller
    const timeoutId = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
    setLoading((prev) => ({ ...prev, scene: true }))
    try {
      const scene = await getScene(settings.backendUrl, targetId, controller.signal)
      updateThread(targetId, (thread) => ({ ...thread, scene }))
    } catch (error) {
      console.error('Failed to refresh scene', error)
    } finally {
      window.clearTimeout(timeoutId)
      if (sceneAbortRef.current === controller) {
        sceneAbortRef.current = null
      }
      setLoading((prev) => ({ ...prev, scene: false }))
    }
  }, [activeThread, settings.backendUrl, updateThread])

  const fetchRenders = useCallback(async (threadId?: string) => {
    const targetId = threadId ?? activeThread?.id
    if (!targetId) return
    if (rendersAbortRef.current) {
      rendersAbortRef.current.abort()
    }
    const controller = new AbortController()
    rendersAbortRef.current = controller
    const timeoutId = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
    setLoading((prev) => ({ ...prev, renders: true }))
    try {
      const renders = await getSceneRenders(settings.backendUrl, targetId, controller.signal)
      updateThread(targetId, (thread) => ({ ...thread, renders }))
    } catch (error) {
      console.error('Failed to fetch renders', error)
    } finally {
      window.clearTimeout(timeoutId)
      if (rendersAbortRef.current === controller) {
        rendersAbortRef.current = null
      }
      setLoading((prev) => ({ ...prev, renders: false }))
    }
  }, [activeThread, settings.backendUrl, updateThread])

  const fetchGltf = useCallback(async (threadId?: string) => {
    const targetId = threadId ?? activeThread?.id
    if (!targetId) return
    if (gltfAbortRef.current) {
      gltfAbortRef.current.abort()
    }
    const controller = new AbortController()
    gltfAbortRef.current = controller
    const timeoutId = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
    setLoading((prev) => ({ ...prev, gltf: true }))
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
    } finally {
      window.clearTimeout(timeoutId)
      if (gltfAbortRef.current === controller) {
        gltfAbortRef.current = null
      }
      setLoading((prev) => ({ ...prev, gltf: false }))
    }
  }, [activeThread, settings.backendUrl, updateThread])

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
    if (!activeThread.scene) {
      window.alert('No scene available. Refresh the scene before downloading.')
      return
    }
    setLoading((prev) => ({ ...prev, download: true }))
    try {
      const blob = await getSceneGltf(settings.backendUrl, activeThread.id)
      const filename = `scene-${activeThread.id}.glb`
      downloadBlob(blob, filename)
    } catch (error) {
      window.alert(`Failed to download GLTF: ${String(error)}`)
    } finally {
      setLoading((prev) => ({ ...prev, download: false }))
    }
  }, [activeThread, settings.backendUrl])

  const downloadBlend = useCallback(async () => {
    if (!activeThread) return
    if (!activeThread.scene) {
      window.alert('No scene available. Refresh the scene before downloading.')
      return
    }
    setLoading((prev) => ({ ...prev, download: true }))
    try {
      const blob = await getSceneBlend(settings.backendUrl, activeThread.id)
      const filename = `scene-${activeThread.id}.blend`
      downloadBlob(blob, filename)
    } catch (error) {
      window.alert(`Failed to download BLEND: ${String(error)}`)
    } finally {
      setLoading((prev) => ({ ...prev, download: false }))
    }
  }, [activeThread, settings.backendUrl])

  useEffect(() => {
    if (!activeThread) return
    const threadId = activeThread.id
    if (!requestedSceneRef.current.has(threadId) && !loading.scene && !activeThread.scene) {
      requestedSceneRef.current.add(threadId)
      refreshScene(threadId)
    }
  }, [
    activeThread?.id,
    activeThread?.scene,
    loading.scene,
    refreshScene
  ])

  useEffect(() => {
    if (!activeThread) return
    const threadId = activeThread.id
    if (!loadedReferenceImagesRef.current.has(threadId)) {
      loadedReferenceImagesRef.current.add(threadId)
      void refreshReferenceImages(threadId)
    }
  }, [activeThread?.id, refreshReferenceImages])

  return (
    <div className="app-shell">
      <aside className={`sidebar ${isSidebarCollapsed ? 'collapsed' : ''}`}>
        <div className="sidebar-header">
          {!isSidebarCollapsed && (
            <div className="sidebar-titles">
              <div className="app-title">3D Scene Agent</div>
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

        <TopBar
          environment={environment}
          onEnvironmentChange={setEnvironment}
          onRefreshScene={refreshScene}
          onFetchRenders={fetchRenders}
          onLoadGltf={fetchGltf}
          onDownloadGltf={downloadGltf}
          onDownloadBlend={downloadBlend}
          autoRefreshScene={settings.autoRefreshScene}
          onAutoRefreshChange={(enabled) => setSettings((prev) => ({ ...prev, autoRefreshScene: enabled }))}
          sceneCollapsed={settings.sceneTabCollapsed}
          onSceneToggle={toggleSceneTab}
          onOpenSettings={() => setShowSettings(true)}
          isSceneLoading={loading.scene}
          isRendersLoading={loading.renders}
          isGltfLoading={loading.gltf}
          isDownloadLoading={loading.download}
          isDownloadDisabled={!activeThread?.scene}
          canRunActions={Boolean(activeThread)}
          backendStatus={backendStatus}
          backendMode={backendMode}
          backendUrl={settings.backendUrl}
        />

        <div className={`workspace ${settings.sceneTabCollapsed ? 'is-scene-collapsed' : ''}`}>
          <section className="workspace-scene">
            {activeThread ? (
              <SceneTab
                scene={activeThread.scene ?? null}
                renders={activeThread.renders ?? []}
                gltfUrl={activeThread.gltfUrl ?? null}
                environment={environment}
                loading={{
                  scene: loading.scene,
                  renders: loading.renders
                }}
                collapsed={settings.sceneTabCollapsed}
                onToggleCollapse={toggleSceneTab}
              />
            ) : (
              <div className="empty-state">Create a conversation to see scene info.</div>
            )}
          </section>
          <section className="workspace-chat">
            <ChatTab
              thread={activeThread}
              isStreaming={isStreaming}
              onSend={handleSend}
              onStop={handleStop}
            />
          </section>
        </div>
      </main>
    </div>
  )
}

export default App
