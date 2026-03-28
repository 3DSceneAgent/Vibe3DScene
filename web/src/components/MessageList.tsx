import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { Message } from '../state/types'
import { ToolResultBlock } from './ToolResultBlock'
import { MarkdownMessage } from './MarkdownMessage'
import { parseTodos } from '../utils/message'

type MessageListProps = {
  messages: Message[]
  backendUrl: string
  streamStatus: 'streaming' | 'complete'
  onRetryTurn?: (turnId: string) => void
}

const STICKY_BOTTOM_THRESHOLD_PX = 48

function isNearBottom(container: HTMLDivElement): boolean {
  const distanceToBottom = container.scrollHeight - container.scrollTop - container.clientHeight
  return distanceToBottom <= STICKY_BOTTOM_THRESHOLD_PX
}

type ConversationTurn = {
  key: string
  userMessage: Message | null
  agentMessages: Message[]
}

function groupMessagesIntoConversationTurns(messages: Message[]): ConversationTurn[] {
  const turns: ConversationTurn[] = []
  let currentTurn: ConversationTurn | null = null

  const flushCurrentTurn = () => {
    if (!currentTurn) return
    turns.push(currentTurn)
    currentTurn = null
  }

  for (const message of messages) {
    if (message.role === 'user') {
      flushCurrentTurn()
      currentTurn = {
        key: message.id,
        userMessage: message,
        agentMessages: []
      }
      continue
    }

    if (!currentTurn) {
      currentTurn = {
        key: message.id,
        userMessage: null,
        agentMessages: [message]
      }
      continue
    }

    currentTurn.agentMessages.push(message)
  }

  flushCurrentTurn()
  return turns
}

export function MessageList({ messages, backendUrl, streamStatus, onRetryTurn }: MessageListProps) {
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

  const turns = useMemo(() => groupMessagesIntoConversationTurns(messages), [messages])

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
      {messages.length === 0 && <div className="muted">Let&apos;s build something!</div>}
      {turns.map((turn, index) => (
        <ConversationTurnItem
          key={turn.key}
          turn={turn}
          backendUrl={backendUrl}
          isActiveTurn={streamStatus === 'streaming' && index === turns.length - 1}
          isLatestTurn={index === turns.length - 1}
          onRetryTurn={onRetryTurn}
        />
      ))}
    </div>
  )
}

const ConversationTurnItem = memo(
  function ConversationTurnItem({
    turn,
    backendUrl,
    isActiveTurn,
    isLatestTurn,
    onRetryTurn
  }: {
    turn: ConversationTurn
    backendUrl: string
    isActiveTurn: boolean
    isLatestTurn: boolean
    onRetryTurn?: (turnId: string) => void
  }) {
    return (
      <div className="conversation-turn">
        {turn.userMessage && <UserMessageItem message={turn.userMessage} backendUrl={backendUrl} />}
        {(turn.agentMessages.length > 0 || isActiveTurn) && (
          <AssistantTurn
            messages={turn.agentMessages}
            backendUrl={backendUrl}
            isActiveTurn={isActiveTurn}
            retryTurnId={isLatestTurn ? turn.userMessage?.turnId : undefined}
            onRetryTurn={onRetryTurn}
          />
        )}
      </div>
    )
  },
  (prev, next) =>
    prev.turn === next.turn &&
    prev.backendUrl === next.backendUrl &&
    prev.isActiveTurn === next.isActiveTurn &&
    prev.isLatestTurn === next.isLatestTurn &&
    prev.onRetryTurn === next.onRetryTurn
)

const UserMessageItem = memo(
  function UserMessageItem({ message, backendUrl }: { message: Message; backendUrl: string }) {
    const attachedImages = message.attachedImages ?? []

    return (
      <div className="message-row user">
        <div className="message-bubble user">
          {attachedImages.length > 0 && (
            <div className="message-attachments" aria-label="Attached images">
              {attachedImages.map((image) => (
                <div className="message-attachment" key={image.id}>
                  {image.previewUrl ? (
                    <img src={image.previewUrl} alt={image.filename} />
                  ) : (
                    <div className="message-attachment-placeholder">{image.filename}</div>
                  )}
                </div>
              ))}
            </div>
          )}
          <div className="message-content">
            <MarkdownMessage content={message.content || ' '} backendUrl={backendUrl} />
          </div>
        </div>
      </div>
    )
  },
  (prev, next) => prev.message === next.message && prev.backendUrl === next.backendUrl
)

const AssistantTurn = memo(
  function AssistantTurn({
    messages,
    backendUrl,
    isActiveTurn,
    retryTurnId,
    onRetryTurn
  }: {
    messages: Message[]
    backendUrl: string
    isActiveTurn: boolean
    retryTurnId?: string
    onRetryTurn?: (turnId: string) => void
  }) {
    const hasPendingTool = messages.some(
      (message) => message.role === 'tool' && message.status === 'streaming'
    )
    const hasError = messages.some((message) => message.status === 'error')
    const showThinkingFooter = isActiveTurn && !hasPendingTool && !hasError
    const canRetry = Boolean(retryTurnId && onRetryTurn && (!isActiveTurn || hasError))

    return (
      <div className="assistant-turn">
        {messages.map((message) => (
          <AssistantTurnItem key={message.id} message={message} backendUrl={backendUrl} />
        ))}
        {showThinkingFooter && <TurnThinkingFooter />}
        {canRetry && retryTurnId && onRetryTurn && (
          <div className="assistant-turn-actions">
            <button
              type="button"
              className="assistant-turn-retry-btn"
              aria-label="重试"
              data-label="重试"
              title="重试"
              onClick={() => onRetryTurn(retryTurnId)}
            >
              <svg
                className="assistant-turn-retry-icon"
                viewBox="0 0 24 24"
                aria-hidden="true"
              >
                <path d="M20 6v5h-5" />
                <path d="M20 11a8 8 0 1 0 2.1 5.4" />
              </svg>
            </button>
          </div>
        )}
      </div>
    )
  },
  (prev, next) =>
    prev.messages === next.messages &&
    prev.backendUrl === next.backendUrl &&
    prev.isActiveTurn === next.isActiveTurn &&
    prev.retryTurnId === next.retryTurnId &&
    prev.onRetryTurn === next.onRetryTurn
)

const AssistantTurnItem = memo(
  function AssistantTurnItem({ message, backendUrl }: { message: Message; backendUrl: string }) {
    const todos = useMemo(() => (message.raw ? parseTodos(message.raw) : []), [message.raw])

    if (message.role === 'tool') {
      return <ToolResultBlock message={message} backendUrl={backendUrl} />
    }

    const showThinkingDetails = message.thinkingActive === true
    const isError = message.status === 'error'
    const hasContent = message.content && message.content.trim().length > 0

    return (
      <div className={`assistant-turn-segment ${isError ? 'is-error' : ''}`}>
        {message.thinking && (
          <ThinkingBlock thinking={message.thinking} isThinking={showThinkingDetails} />
        )}
        {todos.length > 0 && <TodosBlock todos={todos} />}
        {hasContent && (
          <div className="message-content">
            <MarkdownMessage content={message.content} backendUrl={backendUrl} />
          </div>
        )}
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

function TurnThinkingFooter() {
  return (
    <div className="turn-thinking-footer" role="status" aria-live="polite">
      <span className="turn-thinking-label sweep-active">Thinking</span>
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
