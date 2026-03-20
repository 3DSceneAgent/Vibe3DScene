import { useState } from 'react'
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

export function TodoPanel({ todos, activeTodoId = null, fastMode = false, isStreaming = false }: TodoPanelProps) {
  const [collapsed, setCollapsed] = useState(true)
  const hasOpenTodos = todos.some(
    (todo) => todo.status === 'pending' || todo.status === 'in_progress'
  )

  if (todos.length === 0 || (fastMode && !hasOpenTodos)) {
    return null
  }

  const resolvedActiveTodoId = (() => {
    if (typeof activeTodoId === 'string' && activeTodoId.trim()) {
      return activeTodoId.trim()
    }
    const firstPending = todos.find((todo) => todo.status === 'pending')
    return firstPending?.id ?? null
  })()

  const displayTodos: TodoDisplayItem[] = todos.map((todo) => {
    const isResolvedActive = resolvedActiveTodoId != null && todo.id === resolvedActiveTodoId
    const displayStatus =
      isStreaming && isResolvedActive && (todo.status === 'pending' || todo.status === 'in_progress')
        ? 'in_progress'
        : todo.status
    return { ...todo, displayStatus }
  })

  const canCollapse = todos.length > 1
  const completedCount = displayTodos.filter((todo) => todo.displayStatus === 'completed').length
  const nextTodo =
    displayTodos.find((todo) => todo.displayStatus === 'in_progress') ??
    displayTodos.find((todo) => todo.displayStatus === 'pending') ??
    null
  const latestTodo = displayTodos[displayTodos.length - 1]
  const inProgressTodo = displayTodos.find((todo) => todo.displayStatus === 'in_progress') ?? null
  const firstPendingTodo = displayTodos.find((todo) => todo.displayStatus === 'pending') ?? null
  const collapsedTodo = inProgressTodo ?? firstPendingTodo ?? latestTodo
  const visibleTodos = collapsed && collapsedTodo ? [collapsedTodo] : displayTodos

  return (
    <section className="todo-panel" aria-label="Current plan">
      <div className="todo-panel-header">
        <div>
          <div className="todo-panel-title">Current plan</div>
          {!collapsed && (
            <div className="todo-panel-meta">
              {completedCount}/{todos.length} completed
              {nextTodo ? ` · next: ${nextTodo.description}` : ''}
            </div>
          )}
        </div>
        {canCollapse && (
          <button className="text-btn todo-panel-toggle" onClick={() => setCollapsed((prev) => !prev)}>
            {collapsed ? 'Show all' : 'Collapse'}
          </button>
        )}
      </div>
      <div className="todo-panel-list">
        {visibleTodos.map((todo) => (
          <div key={todo.id} className={`todo-panel-item status-${todo.displayStatus}`}>
            <span className="todo-status-icon" aria-hidden="true" />
            <span className={todo.displayStatus === 'completed' ? 'todo-text-completed' : 'todo-text'}>
              {todo.description}
            </span>
          </div>
        ))}
      </div>
    </section>
  )
}
