import type { Thread } from '../state/types'

type ThreadListProps = {
  threads: Thread[]
  activeId: string | null
  onSelect: (threadId: string) => void
  onDelete: (threadId: string) => void
  onReleaseRuntime?: (threadId: string) => void
  onNew: () => void
  creating?: boolean
  createDisabled?: boolean
  releasingThreadId?: string | null
  createError?: string | null
  createHint?: string | null
  quotaHint?: string | null
  collapsed?: boolean
}

export function ThreadList({
  threads,
  activeId,
  onSelect,
  onDelete,
  onReleaseRuntime,
  onNew,
  creating = false,
  createDisabled = false,
  releasingThreadId = null,
  createError = null,
  createHint = null,
  quotaHint = null,
  collapsed = false
}: ThreadListProps) {
  return (
    <div className={`thread-list ${collapsed ? 'collapsed' : ''}`}>
      <button
        className={`primary-btn ${collapsed ? 'icon-btn' : ''}`}
        onClick={onNew}
        disabled={createDisabled || creating}
      >
        {collapsed ? '+' : creating ? 'Creating...' : 'New Chat'}
      </button>
      {!collapsed && quotaHint && <div className="thread-create-hint">{quotaHint}</div>}
      {!collapsed && createHint && <div className="thread-create-hint action">{createHint}</div>}
      {!collapsed && createError && <div className="thread-create-error">{createError}</div>}
      <div className={`thread-items ${collapsed ? 'collapsed' : ''}`}>
        {threads.length === 0 && !collapsed && <div className="muted">No conversations yet</div>}
        {threads.map((thread) => {
          const label = thread.title || 'Untitled'
          const shortLabel = label.trim().charAt(0).toUpperCase() || '?'
          const occupyingResources = Boolean(thread.occupyingResources)
          const isReleasing = releasingThreadId === thread.id
          const runtimeLabel = occupyingResources ? 'Runtime in use' : 'Runtime released'
          return (
            <div
              key={thread.id}
              className={`thread-item ${collapsed ? 'compact' : ''} ${thread.id === activeId ? 'active' : ''} ${occupyingResources ? 'occupying' : 'released'}`}
              onClick={() => onSelect(thread.id)}
              title={collapsed ? label : undefined}
            >
              <div className="thread-title-row">
                <div className="thread-title">{collapsed ? shortLabel : label}</div>
                {!collapsed && (
                  <span
                    className={`thread-runtime-dot ${occupyingResources ? 'occupied' : 'free'}`}
                    title={runtimeLabel}
                    aria-label={runtimeLabel}
                  />
                )}
              </div>
              {!collapsed && (
                <>
                  <div className="thread-meta">
                    {thread.messages.length} messages · {new Date(thread.createdAt).toLocaleDateString()}
                  </div>
                  <div className="thread-actions">
                    {onReleaseRuntime && occupyingResources && (
                      <button
                        className="ghost-btn thread-action-btn"
                        onClick={(event) => {
                          event.stopPropagation()
                          onReleaseRuntime(thread.id)
                        }}
                        disabled={isReleasing}
                      >
                        {isReleasing ? 'Releasing...' : 'Release'}
                      </button>
                    )}
                    <button
                      className="ghost-btn thread-action-btn"
                      onClick={(event) => {
                        event.stopPropagation()
                        onDelete(thread.id)
                      }}
                    >
                      Delete
                    </button>
                  </div>
                </>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
