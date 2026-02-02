import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { streamChat, getScene, getSceneRenders, getSceneGltf } from './api/client'
import type { StreamEvent, TodoItem } from './api/types'
import { ChatTab } from './components/ChatTab'
import { SceneTab } from './components/SceneTab'
import { SettingsPanel } from './components/SettingsPanel'
import { TopBar } from './components/TopBar'
import { ThreadList } from './components/ThreadList'
import { loadSettings, loadThreads, saveSettings, saveThreads } from './state/storage'
import type { Message, Thread } from './state/types'
import {
  applyStreamingDeltaWithId,
  extractMessageContent,
  isHumanMessage,
  isToolMessage,
  parseThinking
} from './utils/message'
import { downloadBlob } from './utils/download'
import './App.css'

function App() {
  const [threads, setThreads] = useState<Thread[]>(() => loadThreads())
  const [activeThreadId, setActiveThreadId] = useState<string | null>(() => loadThreads()[0]?.id ?? null)
  const [settings, setSettings] = useState(() => loadSettings())
  const [environment, setEnvironment] = useState<'studio' | 'warm' | 'cool'>('studio')
  const [isStreaming, setIsStreaming] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false)
  const [loading, setLoading] = useState({
    scene: false,
    renders: false,
    gltf: false,
    download: false
  })
  const streamAbortRef = useRef<AbortController | null>(null)
  const requestedSceneRef = useRef<Set<string>>(new Set())
  const previousAssistantContentRef = useRef<string | null>(null)
  const knownStreamIdsRef = useRef<Set<string>>(new Set())
  const receivedDeltaRef = useRef(false)

  const activeThread = useMemo(
    () => threads.find((thread) => thread.id === activeThreadId) ?? null,
    [threads, activeThreadId]
  )

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
  }, [settings])

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
      gltfUrl: null
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
      return prev.filter((thread) => thread.id !== threadId)
    })
    requestedSceneRef.current.delete(threadId)
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

  const handleSend = async (text: string) => {
    if (!activeThread) {
      return
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

    updateThread(activeThread.id, (thread) => {
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
    try {
      await streamChat({
        baseUrl: settings.backendUrl,
        message: text,
        threadId: activeThread.id,
        signal: abortController.signal,
        onEvent: (event: StreamEvent) => {
          const updateAssistant = (updater: (message: Message) => Message) => {
            updateThread(activeThread.id, (thread) => ({
              ...thread,
              messages: thread.messages.map((message) =>
                message.id === assistantId ? updater(message) : message
              )
            }))
          }

          if (event.error) {
            updateAssistant((message) => ({ ...message, content: `Error: ${event.error}`, status: 'error' }))
            return
          }

          if (event.todos && event.todos.length > 0) {
            updateThread(activeThread.id, (thread) => ({
              ...thread,
              todos: mergeTodos(thread.todos, event.todos || [])
            }))
          }

          if (event.delta) {
            receivedDeltaRef.current = true
            if (event.message_id) {
              knownStreamIdsRef.current.add(event.message_id)
            }
            updateAssistant((message) => {
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
            updateAssistant((message) => ({
              ...message,
              content: parsed.text,
              thinking: parsed.thinking,
              raw,
              streamId,
              status: 'streaming'
            }))
          }
        }
      })
    } catch (error) {
      updateThread(activeThread.id, (thread) => ({
        ...thread,
        messages: thread.messages.map((message) =>
          message.id === assistantId
            ? { ...message, content: `Error: ${String(error)}`, status: 'error' }
            : message
        )
      }))
    } finally {
      updateThread(activeThread.id, (thread) => ({
        ...thread,
        messages: thread.messages.map((message) =>
          message.id === assistantId && message.status !== 'error'
            ? { ...message, status: 'final' }
            : message
        )
      }))
      setIsStreaming(false)
    }
  }

  const refreshScene = useCallback(async () => {
    if (!activeThread) return
    setLoading((prev) => ({ ...prev, scene: true }))
    try {
      const scene = await getScene(settings.backendUrl, activeThread.id)
      updateThread(activeThread.id, (thread) => ({ ...thread, scene }))
    } finally {
      setLoading((prev) => ({ ...prev, scene: false }))
    }
  }, [activeThread, settings.backendUrl, updateThread])

  const fetchRenders = useCallback(async () => {
    if (!activeThread) return
    setLoading((prev) => ({ ...prev, renders: true }))
    try {
      const renders = await getSceneRenders(settings.backendUrl, activeThread.id)
      updateThread(activeThread.id, (thread) => ({ ...thread, renders }))
    } finally {
      setLoading((prev) => ({ ...prev, renders: false }))
    }
  }, [activeThread, settings.backendUrl, updateThread])

  const fetchGltf = useCallback(async () => {
    if (!activeThread) return
    setLoading((prev) => ({ ...prev, gltf: true }))
    try {
      const blob = await getSceneGltf(settings.backendUrl, activeThread.id)
      const nextUrl = URL.createObjectURL(blob)
      updateThread(activeThread.id, (thread) => {
        if (thread.gltfUrl) {
          URL.revokeObjectURL(thread.gltfUrl)
        }
        return { ...thread, gltfUrl: nextUrl }
      })
    } finally {
      setLoading((prev) => ({ ...prev, gltf: false }))
    }
  }, [activeThread, settings.backendUrl, updateThread])

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

  useEffect(() => {
    if (!activeThread) return
    const threadId = activeThread.id
    if (!requestedSceneRef.current.has(threadId) && !loading.scene && !activeThread.scene) {
      requestedSceneRef.current.add(threadId)
      refreshScene()
    }
  }, [
    activeThread?.id,
    activeThread?.scene,
    loading.scene,
    refreshScene
  ])

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
          onOpenSettings={() => setShowSettings(true)}
          isSceneLoading={loading.scene}
          isRendersLoading={loading.renders}
          isGltfLoading={loading.gltf}
          isDownloadLoading={loading.download}
          isDownloadDisabled={!activeThread?.scene}
          canRunActions={Boolean(activeThread)}
        />

        <div className="workspace">
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
              />
            ) : (
              <div className="empty-state">Create a conversation to see scene info.</div>
            )}
          </section>
          <section className="workspace-chat">
            <ChatTab thread={activeThread} isStreaming={isStreaming} onSend={handleSend} />
          </section>
        </div>
      </main>
    </div>
  )
}

export default App
