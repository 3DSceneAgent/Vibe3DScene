import { useCallback, useEffect, useRef, useState } from 'react'
import type { Thread } from '../state/types'
import { countConversationMessages } from '../utils/message'

type ThreadContextMenuState = {
  threadId: string
  left: number
  top: number
}

type ThreadIdDialogState = {
  threadId: string
  title: string
}

type DeleteThreadDialogState = {
  threadId: string
  title: string
}

type DeleteAllDialogState = {
  count: number
}

const THREAD_CONTEXT_MENU_WIDTH = 124

async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // Fall back to a hidden textarea below.
  }

  try {
    const textarea = document.createElement('textarea')
    textarea.value = text
    textarea.setAttribute('readonly', 'true')
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    textarea.style.pointerEvents = 'none'
    document.body.appendChild(textarea)
    textarea.select()
    textarea.setSelectionRange(0, text.length)
    const copied = document.execCommand('copy')
    document.body.removeChild(textarea)
    return copied
  } catch {
    return false
  }
}

type ThreadListProps = {
  threads: Thread[]
  activeId: string | null
  onSelect: (threadId: string) => void
  onDelete: (threadId: string) => void
  onRename: (threadId: string, title: string) => void
  onDeleteAll?: () => void
  onClaimRuntime?: (threadId: string) => void
  onReleaseRuntime?: (threadId: string) => void
  onNew: () => void
  creating?: boolean
  createDisabled?: boolean
  claimingThreadId?: string | null
  releasingThreadId?: string | null
  createError?: string | null
  createHint?: string | null
  quotaHint?: string | null
  collapsed?: boolean
}

function getThreadLastActivityMs(thread: Thread): number {
  const createdAtMs = Number.isFinite(thread.createdAt) ? thread.createdAt : 0
  const updatedAtMs = Number.isFinite(thread.updatedAtMs) ? Number(thread.updatedAtMs) : 0
  return Math.max(createdAtMs, updatedAtMs)
}

function formatThreadLastActivity(timestampMs: number): string {
  if (!Number.isFinite(timestampMs) || timestampMs <= 0) {
    return 'No activity yet'
  }
  return new Date(timestampMs).toLocaleString([], {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  })
}

function clampMenuPosition(clientX: number, clientY: number, itemCount: number) {
  const menuWidth = THREAD_CONTEXT_MENU_WIDTH
  const menuHeight = itemCount * 30 + 12
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
  onClaimRuntime,
  onReleaseRuntime,
  onNew,
  creating = false,
  createDisabled = false,
  claimingThreadId = null,
  releasingThreadId = null,
  createError = null,
  createHint = null,
  quotaHint = null,
  collapsed = false
}: ThreadListProps) {
  const [showQuotaHelp, setShowQuotaHelp] = useState(false)
  const [menuState, setMenuState] = useState<ThreadContextMenuState | null>(null)
  const [editingThreadId, setEditingThreadId] = useState<string | null>(null)
  const [editingTitle, setEditingTitle] = useState('')
  const [threadIdDialog, setThreadIdDialog] = useState<ThreadIdDialogState | null>(null)
  const [deleteThreadDialog, setDeleteThreadDialog] = useState<DeleteThreadDialogState | null>(null)
  const [deleteAllDialog, setDeleteAllDialog] = useState<DeleteAllDialogState | null>(null)
  const [threadIdCopied, setThreadIdCopied] = useState(false)
  const renameInputRef = useRef<HTMLInputElement | null>(null)
  const skipRenameBlurCommitRef = useRef(false)
  const copyResetTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const openDeleteAllDialog = useCallback(() => {
    setDeleteAllDialog({ count: threads.length })
  }, [threads.length])

  const closeDeleteAllDialog = useCallback(() => {
    setDeleteAllDialog(null)
  }, [])

  const visibleThreads = [...threads].sort((a, b) => {
    const activityDiff = getThreadLastActivityMs(b) - getThreadLastActivityMs(a)
    if (activityDiff !== 0) {
      return activityDiff
    }
    const createdDiff =
      (Number.isFinite(b.createdAt) ? b.createdAt : 0) - (Number.isFinite(a.createdAt) ? a.createdAt : 0)
    if (createdDiff !== 0) {
      return createdDiff
    }
    return a.id.localeCompare(b.id)
  })

  useEffect(() => {
    if (!editingThreadId || !renameInputRef.current) {
      return
    }
    renameInputRef.current.focus()
    renameInputRef.current.select()
  }, [editingThreadId])

  useEffect(() => {
    if (!menuState && !editingThreadId && !threadIdDialog && !deleteThreadDialog && !deleteAllDialog) {
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
      if (threadIdDialog) {
        setThreadIdDialog(null)
      }
      if (deleteThreadDialog) {
        setDeleteThreadDialog(null)
      }
      if (deleteAllDialog) {
        setDeleteAllDialog(null)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => {
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [deleteAllDialog, deleteThreadDialog, editingThreadId, menuState, threadIdDialog])

  useEffect(() => {
    return () => {
      if (copyResetTimerRef.current) {
        clearTimeout(copyResetTimerRef.current)
      }
    }
  }, [])

  const openThreadMenu = useCallback(
    (thread: Thread, clientX: number, clientY: number) => {
      const hasRuntimeAction = Boolean(onClaimRuntime) || Boolean(onReleaseRuntime)
      const menuItemCount = hasRuntimeAction ? 4 : 3
      const position = clampMenuPosition(clientX, clientY, menuItemCount)
      setMenuState({
        threadId: thread.id,
        left: position.left,
        top: position.top
      })
    },
    [onClaimRuntime, onReleaseRuntime]
  )

  const closeThreadMenu = useCallback(() => {
    setMenuState(null)
  }, [])

  const openThreadIdDialog = useCallback((thread: Thread) => {
    setMenuState(null)
    setThreadIdCopied(false)
    if (copyResetTimerRef.current) {
      clearTimeout(copyResetTimerRef.current)
      copyResetTimerRef.current = null
    }
    setThreadIdDialog({
      threadId: thread.id,
      title: thread.title || 'Untitled'
    })
  }, [])

  const closeThreadIdDialog = useCallback(() => {
    setThreadIdDialog(null)
  }, [])

  const openDeleteThreadDialog = useCallback((thread: Thread) => {
    setMenuState(null)
    setDeleteThreadDialog({
      threadId: thread.id,
      title: thread.title || 'Untitled'
    })
  }, [])

  const closeDeleteThreadDialog = useCallback(() => {
    setDeleteThreadDialog(null)
  }, [])

  const handleCopyThreadId = useCallback(async () => {
    if (!threadIdDialog) {
      return
    }
    const copied = await copyText(threadIdDialog.threadId)
    if (copied) {
      closeThreadIdDialog()
      return
    }
    setThreadIdCopied(copied)
    if (copyResetTimerRef.current) {
      clearTimeout(copyResetTimerRef.current)
    }
    copyResetTimerRef.current = setTimeout(() => {
      setThreadIdCopied(false)
      copyResetTimerRef.current = null
    }, 1800)
  }, [closeThreadIdDialog, threadIdDialog])

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
    ? visibleThreads.find((thread) => thread.id === menuState.threadId) ?? null
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
            className="ghost-btn thread-delete-all-btn"
            onClick={openDeleteAllDialog}
            title="Delete all conversations"
          >
            ✕ Clear
          </button>
        )}
      </div>
      {!collapsed && quotaHint && (
        <div className="thread-create-hint thread-quota-hint">
          <span>{quotaHint}</span>
          <div className="thread-quota-help-anchor">
            <button
              type="button"
              className={`thread-quota-help ${showQuotaHelp ? 'is-active' : ''}`}
              onClick={() => setShowQuotaHelp((v) => !v)}
              aria-label="What are runtime slots?"
            >
              ?
            </button>
            {showQuotaHelp && (
              <>
                <div className="thread-quota-help-backdrop" onClick={() => setShowQuotaHelp(false)} />
                <div className="thread-quota-help-popover">
                  Each conversation can claim one Blender runtime slot.
                  When all slots are in use, sending a message in a new
                  conversation will auto-release the oldest idle slot.
                  You can also manually claim or release via the right-click menu.
                </div>
              </>
            )}
          </div>
        </div>
      )}
      {!collapsed && createHint && <div className="thread-create-hint action">{createHint}</div>}
      {!collapsed && createError && <div className="thread-create-error">{createError}</div>}
      <div className={`thread-items ${collapsed ? 'collapsed' : ''}`}>
        {visibleThreads.length === 0 && !collapsed && <div className="muted">No conversations yet</div>}
        {visibleThreads.map((thread) => {
          const label = thread.title || 'Untitled'
          const shortLabel = label.trim().charAt(0).toUpperCase() || '?'
          const occupyingResources = Boolean(thread.occupyingResources)
          const isEditing = editingThreadId === thread.id
          const isMenuOpen = menuState?.threadId === thread.id
          const runtimeLabel = occupyingResources ? 'Runtime in use' : 'Runtime released'
          const lastActivityMs = getThreadLastActivityMs(thread)
          const messageCount = countConversationMessages(thread.messages)
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
                  {messageCount} {messageCount === 1 ? 'message' : 'messages'} · {formatThreadLastActivity(lastActivityMs)}
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
            className="thread-context-menu thread-context-menu--thread-list"
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
            <button
              type="button"
              className="thread-context-menu-item"
              role="menuitem"
              onClick={() => {
                openThreadIdDialog(menuThread)
              }}
            >
              View chat ID
            </button>
            {menuThread.occupyingResources && onReleaseRuntime && (
              <button
                type="button"
                className="thread-context-menu-item"
                role="menuitem"
                disabled={releasingThreadId === menuThread.id}
                title="Free the Blender slot so another session can use it"
                onClick={() => {
                  closeThreadMenu()
                  onReleaseRuntime(menuThread.id)
                }}
              >
                {releasingThreadId === menuThread.id ? 'Releasing...' : 'Release runtime'}
              </button>
            )}
            {!menuThread.occupyingResources && onClaimRuntime && (
              <button
                type="button"
                className="thread-context-menu-item"
                role="menuitem"
                disabled={claimingThreadId === menuThread.id}
                title="Connect this session to a Blender instance"
                onClick={() => {
                  closeThreadMenu()
                  onClaimRuntime(menuThread.id)
                }}
              >
                {claimingThreadId === menuThread.id ? 'Claiming...' : 'Claim runtime'}
              </button>
            )}
            <button
              type="button"
              className="thread-context-menu-item danger"
              role="menuitem"
              onClick={() => {
                openDeleteThreadDialog(menuThread)
              }}
            >
              Delete
            </button>
          </div>
        </>
      )}
      {threadIdDialog && (
        <div className="thread-id-dialog-overlay" onClick={closeThreadIdDialog}>
          <div className="thread-id-dialog" onClick={(event) => event.stopPropagation()}>
            <div className="thread-id-dialog-header">
              <div>
                <div className="thread-id-dialog-title">Chat ID</div>
                <div className="thread-id-dialog-subtitle" title={threadIdDialog.title}>
                  {threadIdDialog.title}
                </div>
              </div>
              <button
                type="button"
                className="ghost-btn thread-id-dialog-close"
                onClick={closeThreadIdDialog}
              >
                Close
              </button>
            </div>
            <div className="thread-id-dialog-value" title={threadIdDialog.threadId}>
              {threadIdDialog.threadId}
            </div>
            <div className="thread-id-dialog-actions">
              <button
                type="button"
                className="primary-btn thread-id-dialog-copy"
                onClick={() => void handleCopyThreadId()}
              >
                {threadIdCopied ? 'Copied' : 'Copy'}
              </button>
            </div>
          </div>
        </div>
      )}
      {deleteThreadDialog && (
        <div className="thread-id-dialog-overlay" onClick={closeDeleteThreadDialog}>
          <div className="thread-id-dialog" onClick={(event) => event.stopPropagation()}>
            <div className="thread-id-dialog-header">
              <div>
                <div className="thread-id-dialog-title">Delete conversation?</div>
                <div className="thread-id-dialog-subtitle" title={deleteThreadDialog.title}>
                  {deleteThreadDialog.title}
                </div>
              </div>
              <button
                type="button"
                className="ghost-btn thread-id-dialog-close"
                onClick={closeDeleteThreadDialog}
              >
                Cancel
              </button>
            </div>
            <div className="thread-id-dialog-body">
              This removes the conversation history for this chat from the app. This action cannot be undone.
            </div>
            <div className="thread-id-dialog-actions">
              <button type="button" className="ghost-btn" onClick={closeDeleteThreadDialog}>
                Keep Chat
              </button>
              <button
                type="button"
                className="primary-btn danger"
                onClick={() => {
                  onDelete(deleteThreadDialog.threadId)
                  closeDeleteThreadDialog()
                }}
              >
                Delete Chat
              </button>
            </div>
          </div>
        </div>
      )}
      {deleteAllDialog && (
        <div className="thread-id-dialog-overlay" onClick={closeDeleteAllDialog}>
          <div className="thread-id-dialog" onClick={(event) => event.stopPropagation()}>
            <div className="thread-id-dialog-header">
              <div>
                <div className="thread-id-dialog-title">Delete all conversations?</div>
                <div className="thread-id-dialog-subtitle">
                  {deleteAllDialog.count} {deleteAllDialog.count === 1 ? 'chat' : 'chats'}
                </div>
              </div>
              <button
                type="button"
                className="ghost-btn thread-id-dialog-close"
                onClick={closeDeleteAllDialog}
              >
                Cancel
              </button>
            </div>
            <div className="thread-id-dialog-body">
              This removes every conversation history entry from the app. This action cannot be undone.
            </div>
            <div className="thread-id-dialog-actions">
              <button type="button" className="ghost-btn" onClick={closeDeleteAllDialog}>
                Keep Chats
              </button>
              <button
                type="button"
                className="primary-btn danger"
                onClick={() => {
                  onDeleteAll?.()
                  closeDeleteAllDialog()
                }}
              >
                Delete All
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
