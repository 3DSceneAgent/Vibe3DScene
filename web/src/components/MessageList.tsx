import { useEffect, useRef, useState } from 'react'
import type { Message } from '../state/types'
import { LoadingSpinner } from './LoadingSpinner'
import { ToolResultBlock } from './ToolResultBlock'
import { MarkdownMessage } from './MarkdownMessage'
import { parseTodos } from '../utils/message'

type MessageListProps = {
  messages: Message[]
  backendUrl: string
}

export function MessageList({ messages, backendUrl }: MessageListProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!containerRef.current) return
    containerRef.current.scrollTop = containerRef.current.scrollHeight
  }, [messages])

  return (
    <div className="message-list" ref={containerRef}>
      {messages.length === 0 && <div className="muted">Let's build something!</div>}
      {messages.map((message) => (
        <MessageItem key={message.id} message={message} backendUrl={backendUrl} />
      ))}
    </div>
  )
}

function MessageItem({ message, backendUrl }: { message: Message; backendUrl: string }) {
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
  const todos = message.raw ? parseTodos(message.raw) : []
  return (
    <div className={`message-row ${message.role}`}>
      <div className={`message-bubble ${message.role}`}>
        {message.thinking && <ThinkingBlock thinking={message.thinking} />}
        {todos.length > 0 && <TodosBlock todos={todos} />}
        <div className="message-content">
          <MarkdownMessage content={message.content || ' '} backendUrl={backendUrl} />
          {showSpinner && <LoadingSpinner />}
        </div>
      </div>
    </div>
  )
}

function ThinkingBlock({ thinking }: { thinking: string }) {
  const [open, setOpen] = useState(false)

  return (
    <div className="thinking-block">
      <button className="text-btn" onClick={() => setOpen((prev) => !prev)}>
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
          <span className="todo-status-icon">
            {todo.status === 'pending' && '○'}
            {todo.status === 'in_progress' && '⟳'}
            {todo.status === 'completed' && '✓'}
            {todo.status === 'failed' && '✗'}
          </span>
          <span className={todo.status === 'completed' ? 'todo-text-completed' : 'todo-text'}>
            {todo.description}
          </span>
        </div>
      ))}
    </div>
  )
}
