import { useEffect, useRef, useState } from 'react'
import type { Message } from '../state/types'
import { LoadingSpinner } from './LoadingSpinner'

type MessageListProps = {
  messages: Message[]
}

export function MessageList({ messages }: MessageListProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!containerRef.current) return
    containerRef.current.scrollTop = containerRef.current.scrollHeight
  }, [messages])

  return (
    <div className="message-list" ref={containerRef}>
      {messages.length === 0 && <div className="muted">Start the conversation…</div>}
      {messages.map((message) => (
        <MessageItem key={message.id} message={message} />
      ))}
    </div>
  )
}

function MessageItem({ message }: { message: Message }) {
  const showSpinner = message.role === 'assistant' && message.status === 'streaming'
  return (
    <div className={`message-row ${message.role}`}>
      <div className={`message-bubble ${message.role}`}>
        {message.thinking && <ThinkingBlock thinking={message.thinking} />}
        <div className="message-content">
          {message.content || ' '}
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
