import type { Thread } from '../state/types'
import { MessageList } from './MessageList'
import { ChatComposer } from './ChatComposer'

type ChatTabProps = {
  thread: Thread | null
  isStreaming: boolean
  onSend: (message: string) => void
}

export function ChatTab({ thread, isStreaming, onSend }: ChatTabProps) {
  if (!thread) {
    return <div className="empty-state">Create a conversation to begin.</div>
  }

  return (
    <div className="chat-tab">
      <MessageList messages={thread.messages} />
      <ChatComposer disabled={isStreaming} onSend={onSend} />
    </div>
  )
}
