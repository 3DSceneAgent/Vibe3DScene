import { useEffect, useMemo, useRef, useState } from 'react'
import { streamChat, getScene, getTodos, getSceneRenders, getSceneGltf } from './api/client'
import type { StreamEvent, TodoItem } from './api/types'
import { ChatTab } from './components/ChatTab'
import { SceneTab } from './components/SceneTab'
import { SettingsPanel } from './components/SettingsPanel'
import { ThreadList } from './components/ThreadList'
import { loadSettings, loadThreads, saveSettings, saveThreads } from './state/storage'
import type { Message, Thread } from './state/types'
import { extractMessageContent, hashString, isHumanMessage, isToolMessage, parseThinking } from './utils/message'
import './App.css'

function App() {
  const [threads, setThreads] = useState<Thread[]>(() => loadThreads())
  const [activeThreadId, setActiveThreadId] = useState<string | null>(() => loadThreads()[0]?.id ?? null)
  const [view, setView] = useState<'chat' | 'scene' | 'settings'>('chat')
  const [settings, setSettings] = useState(() => loadSettings())
  const [environment, setEnvironment] = useState<'studio' | 'warm' | 'cool'>('studio')
  const [isStreaming, setIsStreaming] = useState(false)
  const [loading, setLoading] = useState({
    scene: false,
    todos: false,
    renders: false,
    gltf: false
  })
  const streamAbortRef = useRef<AbortController | null>(null)

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

  useEffect(() => {
    if (view !== 'scene' || !activeThread) return
    if (!activeThread.scene && !loading.scene) {
      refreshScene()
    }
    if (activeThread.todos.length === 0 && !loading.todos) {
      refreshTodos()
    }
  }, [view, activeThreadId])

  const updateThread = (threadId: string, updater: (thread: Thread) => Thread) => {
    setThreads((prev) => prev.map((thread) => (thread.id === threadId ? updater(thread) : thread)))
  }

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
    setView('chat')
  }

  const deleteThread = (threadId: string) => {
    setThreads((prev) => {
      const target = prev.find((thread) => thread.id === threadId)
      if (target?.gltfUrl) {
        URL.revokeObjectURL(target.gltfUrl)
      }
      return prev.filter((thread) => thread.id !== threadId)
    })
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
      createdAt: now
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
    const seenHashes = new Set<number>()

    try {
      await streamChat({
        baseUrl: settings.backendUrl,
        message: text,
        threadId: activeThread.id,
        signal: abortController.signal,
        onEvent: (event: StreamEvent) => {
          if (event.error) {
            updateThread(activeThread.id, (thread) => ({
              ...thread,
              messages: thread.messages.map((message) =>
                message.id === assistantId
                  ? { ...message, content: `Error: ${event.error}` }
                  : message
              )
            }))
            return
          }

          if (event.todos && event.todos.length > 0) {
            updateThread(activeThread.id, (thread) => ({
              ...thread,
              todos: mergeTodos(thread.todos, event.todos || [])
            }))
          }

          if (event.messages && event.messages.length > 0) {
            const lastMessage = event.messages[event.messages.length - 1]
            if (isHumanMessage(lastMessage) || isToolMessage(lastMessage)) return
            const raw = extractMessageContent(lastMessage)
            if (!raw) return
            const hash = hashString(raw)
            if (seenHashes.has(hash)) return
            seenHashes.add(hash)
            const parsed = parseThinking(raw)
            updateThread(activeThread.id, (thread) => ({
              ...thread,
              messages: thread.messages.map((message) =>
                message.id === assistantId
                  ? {
                      ...message,
                      content: parsed.text,
                      thinking: parsed.thinking,
                      raw
                    }
                  : message
              )
            }))
          }
        }
      })
    } catch (error) {
      updateThread(activeThread.id, (thread) => ({
        ...thread,
        messages: thread.messages.map((message) =>
          message.id === assistantId
            ? { ...message, content: `Error: ${String(error)}` }
            : message
        )
      }))
    } finally {
      setIsStreaming(false)
    }
  }

  const refreshScene = async () => {
    if (!activeThread) return
    setLoading((prev) => ({ ...prev, scene: true }))
    try {
      const scene = await getScene(settings.backendUrl, activeThread.id)
      updateThread(activeThread.id, (thread) => ({ ...thread, scene }))
    } finally {
      setLoading((prev) => ({ ...prev, scene: false }))
    }
  }

  const refreshTodos = async () => {
    if (!activeThread) return
    setLoading((prev) => ({ ...prev, todos: true }))
    try {
      const todos = await getTodos(settings.backendUrl, activeThread.id)
      updateThread(activeThread.id, (thread) => ({ ...thread, todos }))
    } finally {
      setLoading((prev) => ({ ...prev, todos: false }))
    }
  }

  const fetchRenders = async () => {
    if (!activeThread) return
    setLoading((prev) => ({ ...prev, renders: true }))
    try {
      const renders = await getSceneRenders(settings.backendUrl, activeThread.id)
      updateThread(activeThread.id, (thread) => ({ ...thread, renders }))
    } finally {
      setLoading((prev) => ({ ...prev, renders: false }))
    }
  }

  const fetchGltf = async () => {
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
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-header">
          <div className="app-title">3D Scene Agent</div>
          <div className="app-subtitle">Chat & Scene Console</div>
        </div>
        <ThreadList
          threads={threads}
          activeId={activeThreadId}
          onSelect={(id) => {
            setActiveThreadId(id)
            setView('chat')
          }}
          onDelete={deleteThread}
          onNew={createThread}
        />
        <div className="sidebar-footer">
          <button className="ghost-btn full-width" onClick={() => setView('settings')}>
            Settings
          </button>
        </div>
      </aside>

      <main className="main">
        <div className="topbar">
          <div className="tabs">
            <button
              className={`tab ${view === 'chat' ? 'active' : ''}`}
              onClick={() => setView('chat')}
            >
              Chat
            </button>
            <button
              className={`tab ${view === 'scene' ? 'active' : ''}`}
              onClick={() => setView('scene')}
            >
              Scene
            </button>
            <button
              className={`tab ${view === 'settings' ? 'active' : ''}`}
              onClick={() => setView('settings')}
            >
              Settings
            </button>
          </div>
          {activeThread && view !== 'settings' && (
            <div className="thread-pill">{activeThread.title}</div>
          )}
        </div>

        <div className="main-content">
          {view === 'settings' && <SettingsPanel settings={settings} onChange={setSettings} />}
          {view === 'chat' && (
            <ChatTab thread={activeThread} isStreaming={isStreaming} onSend={handleSend} />
          )}
          {view === 'scene' && activeThread && (
            <SceneTab
              scene={activeThread.scene ?? null}
              todos={activeThread.todos}
              renders={activeThread.renders ?? []}
              gltfUrl={activeThread.gltfUrl ?? null}
              environment={environment}
              onEnvironmentChange={setEnvironment}
              onRefreshScene={refreshScene}
              onRefreshTodos={refreshTodos}
              onFetchRenders={fetchRenders}
              onFetchGltf={fetchGltf}
              loading={loading}
            />
          )}
          {view === 'scene' && !activeThread && (
            <div className="empty-state">Create a conversation to see scene info.</div>
          )}
        </div>
      </main>
    </div>
  )
}

export default App
