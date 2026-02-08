import type { Thread } from '../state/types'
import { MessageList } from './MessageList'
import { ChatComposer } from './ChatComposer'
import { ReferenceImageStrip } from './ReferenceImageStrip'
import type { ReferenceImage } from '../api/types'

type ChatTabProps = {
  thread: Thread | null
  isStreaming: boolean
  onSend: (message: string, files: File[]) => Promise<boolean>
  onStop?: () => void
}

export function ChatTab({ thread, isStreaming, onSend, onStop }: ChatTabProps) {
  if (!thread) {
    return <div className="empty-state">Create a conversation to begin.</div>
  }

  return (
    <div className="chat-tab chat-pane">
      <div className="chat-scroll-area">
        <MessageList messages={thread.messages} />
      </div>
      {thread.referenceImages && thread.referenceImages.length > 0 && (
        <ReferenceImageStrip images={thread.referenceImages as ReferenceImage[]} />
      )}
      <ChatComposer
        disabled={isStreaming}
        onSend={onSend}
        onStop={onStop}
        referenceImagesCount={thread.referenceImages?.length ?? 0}
      />
    </div>
  )
}
