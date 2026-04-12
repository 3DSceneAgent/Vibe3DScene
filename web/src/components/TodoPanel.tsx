import { useEffect, useId, useRef, useState } from 'react'
import type { TodoItem } from '../api/types'

type TodoPanelProps = {
  todos: TodoItem[]
  activeTodoId?: string | null
  fastMode?: boolean
  isStreaming?: boolean
}

type TodoDisplayItem = TodoItem & {
  displayStatus: TodoItem['status']
}

function truncate(text: string, max: number): string {
  const t = text.trim()
  if (t.length <= max) return t
  return `${t.slice(0, Math.max(0, max - 1))}…`
}

function isOpenTodoStatus(status: TodoItem['status']): boolean {
  return status === 'pending' || status === 'in_progress'
}

export function TodoPanel({ todos, activeTodoId = null, fastMode = false, isStreaming = false }: TodoPanelProps) {
  const panelId = useId()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const handleRef = useRef<HTMLButtonElement | null>(null)

  const hasOpenTodos = todos.some(
    (todo) => todo.status === 'pending' || todo.status === 'in_progress'
  )

  useEffect(() => {
    if (!drawerOpen) return

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setDrawerOpen(false)
        handleRef.current?.focus()
      }
    }

    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [drawerOpen])

  if (todos.length === 0 || (fastMode && !hasOpenTodos)) {
    return null
  }

  const resolvedActiveTodoId = (() => {
    if (typeof activeTodoId === 'string' && activeTodoId.trim()) {
      const candidate = activeTodoId.trim()
      const matched = todos.find((todo) => todo.id === candidate)
      if (matched && isOpenTodoStatus(matched.status)) {
        return candidate
      }
    }
    const firstOpen = todos.find((todo) => todo.status === 'in_progress' || todo.status === 'pending')
    return firstOpen?.id ?? null
  })()

  const displayTodos: TodoDisplayItem[] = todos.map((todo) => {
    const isResolvedActive = resolvedActiveTodoId != null && todo.id === resolvedActiveTodoId
    const displayStatus =
      isStreaming && isResolvedActive && (todo.status === 'pending' || todo.status === 'in_progress')
        ? 'in_progress'
        : todo.status
    return { ...todo, displayStatus }
  })

  const doneCount = displayTodos.filter(
    (todo) => todo.displayStatus === 'completed' || todo.displayStatus === 'skipped'
  ).length
  const inProgressTodo = displayTodos.find((todo) => todo.displayStatus === 'in_progress') ?? null
  const firstPendingTodo = displayTodos.find((todo) => todo.displayStatus === 'pending') ?? null
  const latestTodo = displayTodos[displayTodos.length - 1]
  const headlineTodo = inProgressTodo ?? firstPendingTodo ?? latestTodo
  const headline = headlineTodo ? truncate(headlineTodo.description, 72) : 'All clear'
  const openLabel = `Todos (${doneCount}/${todos.length})`

  const handleAriaLabel = `Todos, ${doneCount} of ${todos.length} done. ${headline}`

  return (
    <div
      className={`todo-drawer ${drawerOpen ? 'is-open' : ''} ${isStreaming && hasOpenTodos ? 'is-live' : ''}`}
    >
      <button
        ref={handleRef}
        type="button"
        className="todo-drawer-handle"
        aria-expanded={drawerOpen}
        aria-controls={panelId}
        aria-label={handleAriaLabel}
        id={`${panelId}-handle`}
        onClick={() => setDrawerOpen((prev) => !prev)}
      >
        <span className="todo-drawer-handle-row">
          {drawerOpen ? (
            <span className="todo-drawer-handle-title">{openLabel}</span>
          ) : (
            <>
              <span className="todo-drawer-handle-badge" title={`${doneCount} of ${todos.length} done`}>
                {doneCount}/{todos.length}
              </span>
              <span className="todo-drawer-handle-preview" title={headlineTodo?.description}>
                {headline}
              </span>
            </>
          )}
          <span className={`todo-drawer-handle-chevron ${drawerOpen ? 'is-open' : ''}`} aria-hidden="true" />
        </span>
      </button>

      <div className="todo-drawer-collapse" id={panelId} role="region" aria-label="Todos">
        <div className="todo-drawer-collapse-inner">
          <div className="todo-drawer-scroll" aria-hidden={!drawerOpen}>
            {isStreaming && hasOpenTodos ? (
              <div className="todo-drawer-panel-head todo-drawer-panel-head-running-only">
                <span className="todo-drawer-live-pill sweep-active">Running</span>
              </div>
            ) : null}
            <ul className="todo-drawer-list">
              {displayTodos.map((todo) => (
                <li key={todo.id} className={`todo-drawer-item status-${todo.displayStatus}`}>
                  <span className="todo-status-icon" aria-hidden="true" />
                  <span
                    className={
                      todo.displayStatus === 'completed'
                        ? 'todo-text-completed'
                        : todo.displayStatus === 'skipped'
                          ? 'todo-text-skipped'
                          : 'todo-text'
                    }
                  >
                    {todo.description}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>
    </div>
  )
}
