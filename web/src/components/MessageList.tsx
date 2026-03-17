import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { Message } from '../state/types'
import { LoadingSpinner } from './LoadingSpinner'
import { ToolResultBlock } from './ToolResultBlock'
import { MarkdownMessage } from './MarkdownMessage'
import { parseTodos } from '../utils/message'

type MessageListProps = {
  messages: Message[]
  backendUrl: string
}

const STICKY_BOTTOM_THRESHOLD_PX = 48

function isNearBottom(container: HTMLDivElement): boolean {
  const distanceToBottom = container.scrollHeight - container.scrollTop - container.clientHeight
  return distanceToBottom <= STICKY_BOTTOM_THRESHOLD_PX
}

export function MessageList({ messages, backendUrl }: MessageListProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const shouldStickToBottomRef = useRef(true)
  const scrollRafRef = useRef<number | null>(null)

  useEffect(() => {
    return () => {
      if (scrollRafRef.current !== null) {
        window.cancelAnimationFrame(scrollRafRef.current)
      }
    }
  }, [])

  useLayoutEffect(() => {
    const container = containerRef.current
    if (!container || !shouldStickToBottomRef.current) return

    if (scrollRafRef.current !== null) {
      window.cancelAnimationFrame(scrollRafRef.current)
    }
    scrollRafRef.current = window.requestAnimationFrame(() => {
      container.scrollTop = container.scrollHeight
      scrollRafRef.current = null
    })
  }, [messages])

  return (
    <div
      className="message-list"
      ref={containerRef}
      onScroll={() => {
        const container = containerRef.current
        if (!container) return
        shouldStickToBottomRef.current = isNearBottom(container)
      }}
    >
      {messages.length === 0 && <div className="muted">Let's build something!</div>}
      {messages.map((message) => (
        <MessageItem key={message.id} message={message} backendUrl={backendUrl} />
      ))}
    </div>
  )
}

const MessageItem = memo(
  function MessageItem({ message, backendUrl }: { message: Message; backendUrl: string }) {
    const todos = useMemo(() => (message.raw ? parseTodos(message.raw) : []), [message.raw])
    if (message.role === 'tool') {
      return (
        <div className="message-row tool">
          <div className="message-bubble tool">
            <ToolResultBlock message={message} backendUrl={backendUrl} />
          </div>
        </div>
      )
    }
    const showSpinner = message.role === 'assistant' && message.status === 'streaming'
    const showThinkingSpinner = message.role === 'assistant' && message.thinkingActive === true
    const isAssistantError = message.role === 'assistant' && message.status === 'error'
    return (
      <div className={`message-row ${message.role} ${isAssistantError ? 'error' : ''}`}>
        <div className={`message-bubble ${message.role} ${isAssistantError ? 'error' : ''}`}>
          {message.thinking && (
            <ThinkingBlock
              thinking={message.thinking}
              isThinking={showThinkingSpinner}
            />
          )}
          {todos.length > 0 && <TodosBlock todos={todos} />}
          <div className="message-content">
            <MarkdownMessage content={message.content || ' '} backendUrl={backendUrl} />
            {showSpinner && <LoadingSpinner />}
          </div>
        </div>
      </div>
    )
  },
  (prev, next) => prev.message === next.message && prev.backendUrl === next.backendUrl
)

function ThinkingBlock({ thinking, isThinking = false }: { thinking: string; isThinking?: boolean }) {
  const [manualOpen, setManualOpen] = useState<boolean | null>(null)
  const open = manualOpen ?? isThinking

  return (
    <div className="thinking-block">
      <button
        className="text-btn"
        onClick={() => setManualOpen((prev) => !(prev ?? isThinking))}
      >
        {open ? 'Hide' : 'Show'} thinking
      </button>
      {open && <pre className="thinking-text">{thinking}</pre>}
    </div>
  )
}

function TodosBlock({ todos }: { todos: Array<{ status: string; description: string }> }) {
  return (
    <div className="message-todos">
      {todos.map((todo, index) => (
        <div key={index} className={`message-todo-item status-${todo.status}`}>
          <span className="todo-status-icon" aria-hidden="true" />
          <span className={todo.status === 'completed' ? 'todo-text-completed' : 'todo-text'}>
            {todo.description}
          </span>
        </div>
      ))}
    </div>
  )
}
