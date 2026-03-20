import { useCallback, useEffect, useRef, useState } from 'react'
import type { Thread } from '../state/types'

type ThreadContextMenuState = {
  threadId: string
  left: number
  top: number
}

type ThreadListProps = {
  threads: Thread[]
  activeId: string | null
  onSelect: (threadId: string) => void
  onDelete: (threadId: string) => void
  onRename: (threadId: string, title: string) => void
  onDeleteAll?: () => void
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

function getThreadLastActivityMs(thread: Thread): number {
  const createdAt = Number.isFinite(thread.createdAt) ? thread.createdAt : 0
  const lastMessageAt = thread.messages.reduce((latest, message) => {
    const timestamp = Number(message.createdAt)
    return Number.isFinite(timestamp) ? Math.max(latest, timestamp) : latest
  }, createdAt)
  return Math.max(createdAt, lastMessageAt)
}

function clampMenuPosition(clientX: number, clientY: number, itemCount: number) {
  const menuWidth = 188
  const menuHeight = itemCount * 36 + 16
  const viewportPadding = 12
  const left = Math.min(
    Math.max(viewportPadding, clientX),
    Math.max(viewportPadding, window.innerWidth - menuWidth - viewportPadding)
  )
  const top = Math.min(
    Math.max(viewportPadding, clientY),
    Math.max(viewportPadding, window.innerHeight - menuHeight - viewportPadding)
  )
  return { left, top }
}

export function ThreadList({
  threads,
  activeId,
  onSelect,
  onDelete,
  onRename,
  onDeleteAll,
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
  const [confirmDeleteAll, setConfirmDeleteAll] = useState(false)
  const [menuState, setMenuState] = useState<ThreadContextMenuState | null>(null)
  const [editingThreadId, setEditingThreadId] = useState<string | null>(null)
  const [editingTitle, setEditingTitle] = useState('')
  const confirmTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const renameInputRef = useRef<HTMLInputElement | null>(null)
  const skipRenameBlurCommitRef = useRef(false)

  const handleDeleteAllClick = useCallback(() => {
    if (!confirmDeleteAll) {
      setConfirmDeleteAll(true)
      if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current)
      confirmTimerRef.current = setTimeout(() => setConfirmDeleteAll(false), 3000)
      return
    }
    if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current)
    setConfirmDeleteAll(false)
    onDeleteAll?.()
  }, [confirmDeleteAll, onDeleteAll])

  const sortedThreads = [...threads].sort((a, b) => {
    const activityDiff = getThreadLastActivityMs(b) - getThreadLastActivityMs(a)
    if (activityDiff !== 0) {
      return activityDiff
    }
    return (Number.isFinite(b.createdAt) ? b.createdAt : 0) - (Number.isFinite(a.createdAt) ? a.createdAt : 0)
  })

  useEffect(() => {
    if (!editingThreadId || !renameInputRef.current) {
      return
    }
    renameInputRef.current.focus()
    renameInputRef.current.select()
  }, [editingThreadId])

  useEffect(() => {
    if (!menuState && !editingThreadId) {
      return
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') {
        return
      }
      if (editingThreadId) {
        skipRenameBlurCommitRef.current = true
        setEditingThreadId(null)
        setEditingTitle('')
      }
      if (menuState) {
        setMenuState(null)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => {
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [editingThreadId, menuState])

  const openThreadMenu = useCallback(
    (thread: Thread, clientX: number, clientY: number) => {
      const menuItemCount = onReleaseRuntime && thread.occupyingResources ? 3 : 2
      const position = clampMenuPosition(clientX, clientY, menuItemCount)
      setMenuState({
        threadId: thread.id,
        left: position.left,
        top: position.top
      })
    },
    [onReleaseRuntime]
  )

  const closeThreadMenu = useCallback(() => {
    setMenuState(null)
  }, [])

  const cancelRename = useCallback(() => {
    skipRenameBlurCommitRef.current = false
    setEditingThreadId(null)
    setEditingTitle('')
  }, [])

  const startRename = useCallback((thread: Thread) => {
    skipRenameBlurCommitRef.current = false
    setMenuState(null)
    setEditingThreadId(thread.id)
    setEditingTitle(thread.title || 'Untitled')
  }, [])

  const commitRename = useCallback(
    (thread: Thread) => {
      const nextTitle = editingTitle.trim().slice(0, 120)
      skipRenameBlurCommitRef.current = false
      setEditingThreadId(null)
      setEditingTitle('')
      if (!nextTitle || nextTitle === thread.title) {
        return
      }
      onRename(thread.id, nextTitle)
    },
    [editingTitle, onRename]
  )

  const menuThread = menuState
    ? sortedThreads.find((thread) => thread.id === menuState.threadId) ?? null
    : null

  return (
    <div className={`thread-list ${collapsed ? 'collapsed' : ''}`}>
      <div className="thread-list-header">
        <button
          className={`primary-btn ${collapsed ? 'icon-btn' : ''}`}
          onClick={onNew}
          disabled={createDisabled || creating}
        >
          {collapsed ? '+' : creating ? 'Creating...' : 'New Chat'}
        </button>
        {!collapsed && onDeleteAll && threads.length > 0 && (
          <button
            className={`ghost-btn thread-delete-all-btn ${confirmDeleteAll ? 'is-confirming' : ''}`}
            onClick={handleDeleteAllClick}
            title={confirmDeleteAll ? 'Click again to confirm' : 'Delete all conversations'}
          >
            {confirmDeleteAll ? 'Confirm?' : '✕ Clear'}
          </button>
        )}
      </div>
      {!collapsed && quotaHint && <div className="thread-create-hint">{quotaHint}</div>}
      {!collapsed && createHint && <div className="thread-create-hint action">{createHint}</div>}
      {!collapsed && createError && <div className="thread-create-error">{createError}</div>}
      <div className={`thread-items ${collapsed ? 'collapsed' : ''}`}>
        {sortedThreads.length === 0 && !collapsed && <div className="muted">No conversations yet</div>}
        {sortedThreads.map((thread) => {
          const label = thread.title || 'Untitled'
          const shortLabel = label.trim().charAt(0).toUpperCase() || '?'
          const occupyingResources = Boolean(thread.occupyingResources)
          const isEditing = editingThreadId === thread.id
          const isMenuOpen = menuState?.threadId === thread.id
          const runtimeLabel = occupyingResources ? 'Runtime in use' : 'Runtime released'
          return (
            <div
              key={thread.id}
              className={`thread-item ${collapsed ? 'compact' : ''} ${thread.id === activeId ? 'active' : ''} ${occupyingResources ? 'occupying' : 'released'} ${isMenuOpen ? 'has-open-menu' : ''}`}
              onClick={() => {
                if (isEditing) {
                  return
                }
                onSelect(thread.id)
              }}
              onContextMenu={(event) => {
                if (collapsed) {
                  return
                }
                event.preventDefault()
                event.stopPropagation()
                openThreadMenu(thread, event.clientX, event.clientY)
              }}
              title={collapsed ? label : undefined}
            >
              <div className="thread-title-row">
                {collapsed ? (
                  <div className="thread-title">{shortLabel}</div>
                ) : isEditing ? (
                  <input
                    ref={renameInputRef}
                    className="thread-title-input"
                    value={editingTitle}
                    maxLength={120}
                    onBlur={() => {
                      if (skipRenameBlurCommitRef.current) {
                        skipRenameBlurCommitRef.current = false
                        return
                      }
                      commitRename(thread)
                    }}
                    onChange={(event) => {
                      setEditingTitle(event.target.value.slice(0, 120))
                    }}
                    onClick={(event) => {
                      event.stopPropagation()
                    }}
                    onKeyDown={(event) => {
                      event.stopPropagation()
                      if (event.key === 'Enter') {
                        event.preventDefault()
                        commitRename(thread)
                        return
                      }
                      if (event.key === 'Escape') {
                        event.preventDefault()
                        skipRenameBlurCommitRef.current = true
                        cancelRename()
                      }
                    }}
                  />
                ) : (
                  <div className="thread-title">{label}</div>
                )}
                {!collapsed && (
                  <div className="thread-title-actions">
                    <span
                      className={`thread-runtime-dot ${occupyingResources ? 'occupied' : 'free'}`}
                      title={runtimeLabel}
                      aria-label={runtimeLabel}
                    />
                  </div>
                )}
              </div>
              {!collapsed && (
                <div className="thread-meta">
                  {thread.messages.length} messages · {new Date(thread.createdAt).toLocaleDateString()}
                </div>
              )}
              {!collapsed && (
                <button
                  type="button"
                  className={`ghost-btn icon-btn thread-menu-trigger ${isMenuOpen ? 'is-visible is-active' : ''}`}
                  aria-label={`Open actions for ${label}`}
                  aria-haspopup="menu"
                  aria-expanded={isMenuOpen}
                  onClick={(event) => {
                    event.preventDefault()
                    event.stopPropagation()
                    if (isMenuOpen) {
                      closeThreadMenu()
                      return
                    }
                    const rect = event.currentTarget.getBoundingClientRect()
                    openThreadMenu(thread, rect.right - 12, rect.bottom + 8)
                  }}
                >
                  <span className="thread-menu-trigger-dots" aria-hidden="true">
                    <span className="thread-menu-trigger-dot" />
                    <span className="thread-menu-trigger-dot" />
                    <span className="thread-menu-trigger-dot" />
                  </span>
                </button>
              )}
            </div>
          )
        })}
      </div>
      {menuThread && menuState && (
        <>
          <div className="thread-context-menu-backdrop" onClick={closeThreadMenu} aria-hidden="true" />
          <div
            className="thread-context-menu"
            role="menu"
            aria-label={`Actions for ${menuThread.title || 'Untitled'}`}
            style={{ left: menuState.left, top: menuState.top }}
          >
            <button
              type="button"
              className="thread-context-menu-item"
              role="menuitem"
              onClick={() => {
                startRename(menuThread)
              }}
            >
              Rename
            </button>
            {onReleaseRuntime && menuThread.occupyingResources && (
              <button
                type="button"
                className="thread-context-menu-item"
                role="menuitem"
                disabled={releasingThreadId === menuThread.id}
                onClick={() => {
                  closeThreadMenu()
                  onReleaseRuntime(menuThread.id)
                }}
              >
                {releasingThreadId === menuThread.id ? 'Releasing...' : 'Release runtime'}
              </button>
            )}
            <button
              type="button"
              className="thread-context-menu-item danger"
              role="menuitem"
              onClick={() => {
                closeThreadMenu()
                onDelete(menuThread.id)
              }}
            >
              Delete
            </button>
          </div>
        </>
      )}
    </div>
  )
}
