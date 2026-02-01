import type { Thread } from '../state/types'

type ThreadListProps = {
  threads: Thread[]
  activeId: string | null
  onSelect: (threadId: string) => void
  onDelete: (threadId: string) => void
  onNew: () => void
}

export function ThreadList({ threads, activeId, onSelect, onDelete, onNew }: ThreadListProps) {
  return (
    <div className="thread-list">
      <button className="primary-btn" onClick={onNew}>
        New Chat
      </button>
      <div className="thread-items">
        {threads.length === 0 && <div className="muted">No conversations yet</div>}
        {threads.map((thread) => (
          <div
            key={thread.id}
            className={`thread-item ${thread.id === activeId ? 'active' : ''}`}
            onClick={() => onSelect(thread.id)}
          >
            <div className="thread-title">{thread.title || 'Untitled'}</div>
            <div className="thread-meta">
              {thread.messages.length} messages · {new Date(thread.createdAt).toLocaleDateString()}
            </div>
            <button
              className="ghost-btn"
              onClick={(event) => {
                event.stopPropagation()
                onDelete(thread.id)
              }}
            >
              Delete
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
