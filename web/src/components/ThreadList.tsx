import type { Thread } from '../state/types'

type ThreadListProps = {
  threads: Thread[]
  activeId: string | null
  onSelect: (threadId: string) => void
  onDelete: (threadId: string) => void
  onNew: () => void
  collapsed?: boolean
}

export function ThreadList({
  threads,
  activeId,
  onSelect,
  onDelete,
  onNew,
  collapsed = false
}: ThreadListProps) {
  return (
    <div className={`thread-list ${collapsed ? 'collapsed' : ''}`}>
      <button className={`primary-btn ${collapsed ? 'icon-btn' : ''}`} onClick={onNew}>
        {collapsed ? '+' : 'New Chat'}
      </button>
      <div className={`thread-items ${collapsed ? 'collapsed' : ''}`}>
        {threads.length === 0 && !collapsed && <div className="muted">No conversations yet</div>}
        {threads.map((thread) => {
          const label = thread.title || 'Untitled'
          const shortLabel = label.trim().charAt(0).toUpperCase() || '?'
          return (
            <div
              key={thread.id}
              className={`thread-item ${collapsed ? 'compact' : ''} ${thread.id === activeId ? 'active' : ''}`}
              onClick={() => onSelect(thread.id)}
              title={collapsed ? label : undefined}
            >
              <div className="thread-title">{collapsed ? shortLabel : label}</div>
              {!collapsed && (
                <>
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
                </>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
