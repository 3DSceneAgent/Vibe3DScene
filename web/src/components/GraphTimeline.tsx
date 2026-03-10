import { useEffect, useMemo, useRef, useState } from 'react'
import type { GraphNodeStream, TodoItem } from '../api/types'

type GraphTimelineProps = {
  events: GraphNodeStream[]
  isStreaming?: boolean
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function normalizeTodoStatus(rawStatus: unknown): TodoItem['status'] {
  if (typeof rawStatus !== 'string') return 'pending'
  switch (rawStatus.trim().toLowerCase()) {
    case 'in_progress':
    case 'in progress':
      return 'in_progress'
    case 'completed':
    case 'done':
      return 'completed'
    case 'failed':
    case 'error':
      return 'failed'
    case 'skipped':
      return 'skipped'
    case 'pending':
    default:
      return 'pending'
  }
}

function extractTodosFromPatch(
  patch: Record<string, unknown> | undefined
): Array<Pick<TodoItem, 'id' | 'description' | 'status'>> {
  if (!patch) return []
  const rawTodos = patch.todos
  if (!Array.isArray(rawTodos)) return []
  return rawTodos.flatMap((entry) => {
    if (!isRecord(entry)) return []
    const description = typeof entry.description === 'string' ? entry.description.trim() : ''
    if (!description) return []
    const id = typeof entry.id === 'string' ? entry.id : description
    return [
      {
        id,
        description,
        status: normalizeTodoStatus(entry.status)
      }
    ]
  })
}

function summarizeTodoProgress(todos: Array<Pick<TodoItem, 'status'>>): string | null {
  if (todos.length === 0) return null
  const completed = todos.filter((todo) => todo.status === 'completed').length
  const inProgress = todos.filter((todo) => todo.status === 'in_progress').length
  const pending = todos.filter((todo) => todo.status === 'pending').length
  const skipped = todos.filter((todo) => todo.status === 'skipped').length
  const failed = todos.filter((todo) => todo.status === 'failed').length
  const parts = [`${todos.length} todos`, `${completed} completed`]
  if (inProgress > 0) parts.push(`${inProgress} active`)
  if (pending > 0) parts.push(`${pending} pending`)
  if (skipped > 0) parts.push(`${skipped} skipped`)
  if (failed > 0) parts.push(`${failed} failed`)
  return parts.join(' · ')
}

function stringifyPatchValue(value: unknown): string {
  if (value == null) return 'null'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try {
    const json = JSON.stringify(value)
    if (json.length > 180) return `${json.slice(0, 180)}...`
    return json
  } catch {
    return String(value)
  }
}

function summarizePatch(patch: Record<string, unknown> | undefined): string | null {
  if (!patch) return null
  const prioritizedEntries: Array<[string, string]> = []

  if (typeof patch.task_mode === 'string' && patch.task_mode.trim()) {
    prioritizedEntries.push(['task_mode', patch.task_mode.trim()])
  }
  if (typeof patch.workflow_topology === 'string' && patch.workflow_topology.trim()) {
    prioritizedEntries.push(['workflow_topology', patch.workflow_topology.trim()])
  }
  const todos = extractTodosFromPatch(patch)
  const todoSummary = summarizeTodoProgress(todos)
  if (todoSummary) {
    prioritizedEntries.push(['todos', todoSummary])
  }
  if (typeof patch.active_todo_id === 'string' && patch.active_todo_id.trim()) {
    prioritizedEntries.push(['active_todo_id', patch.active_todo_id.trim()])
  }

  if (prioritizedEntries.length > 0) {
    return prioritizedEntries
      .slice(0, 3)
      .map(([key, value]) => `${key}=${value}`)
      .join(' · ')
  }

  const entries = Object.entries(patch)
    .filter(([key]) => key !== 'todo_versions')
    .slice(0, 3)
  if (entries.length === 0) return null
  return entries.map(([key, value]) => `${key}=${stringifyPatchValue(value)}`).join(' · ')
}

export function GraphTimeline({ events, isStreaming = false }: GraphTimelineProps) {
  const [collapsed, setCollapsed] = useState(true)
  const listRef = useRef<HTMLDivElement | null>(null)
  const latestEvent = useMemo(
    () => (events.length > 0 ? events[events.length - 1] : null),
    [events]
  )
  const latestTodos = useMemo(
    () => extractTodosFromPatch(latestEvent?.state_patch),
    [latestEvent]
  )
  const latestTodoSummary = useMemo(
    () => summarizeTodoProgress(latestTodos),
    [latestTodos]
  )

  useEffect(() => {
    if (collapsed) return
    const list = listRef.current
    if (!list) return
    list.scrollTop = list.scrollHeight
  }, [collapsed, latestEvent])

  return (
    <section className="graph-timeline">
      <div className="graph-timeline-header">
        <div className="graph-timeline-title">Graph timeline</div>
        <div className="graph-timeline-actions">
          {!collapsed && <span className="graph-timeline-count">{events.length} steps</span>}
          <button className="text-btn" onClick={() => setCollapsed((prev) => !prev)}>
            {collapsed ? 'Show' : 'Hide'}
          </button>
        </div>
      </div>
      {collapsed ? (
        <div className="graph-timeline-collapsed">
          {latestEvent ? (
            <div
              className={`graph-timeline-collapsed-chip ${
                isStreaming ? 'is-streaming' : 'is-static'
              }`}
            >
              <span
                className={`graph-timeline-collapsed-dot ${
                  isStreaming ? 'is-streaming' : 'is-static'
                }`}
                aria-hidden="true"
              />
              <div className="graph-timeline-collapsed-copy">
                <span className="graph-timeline-node graph-timeline-collapsed-node">
                  {latestEvent.node}
                </span>
                {latestTodoSummary && (
                  <span className="graph-timeline-collapsed-meta">{latestTodoSummary}</span>
                )}
              </div>
            </div>
          ) : (
            <span className="muted">No node updates yet.</span>
          )}
        </div>
      ) : (
        <div className="graph-timeline-list" ref={listRef}>
          {events.length === 0 && <div className="muted">No node updates yet.</div>}
          {events.map((event) => {
            const summary = summarizePatch(event.state_patch)
            const todos = extractTodosFromPatch(event.state_patch)
            return (
              <div className="graph-timeline-item" key={`${event.request_id}-${event.step_index}-${event.node}`}>
                <div className="graph-timeline-row">
                  <span className="graph-timeline-step">#{event.step_index}</span>
                  <span className="graph-timeline-node">{event.node}</span>
                </div>
                <div className="graph-timeline-keys">updates: {event.update_keys.join(', ') || 'none'}</div>
                {typeof event.message_count === 'number' && event.message_count > 0 && (
                  <div className="graph-timeline-meta">messages: {event.message_count}</div>
                )}
                {summary && <div className="graph-timeline-meta">{summary}</div>}
                {todos.length > 0 && (
                  <div className="graph-timeline-todos">
                    <div className="graph-timeline-todos-label">
                      {event.node === 'plan_node' ? 'Planner output' : 'Todo snapshot'}
                    </div>
                    {todos.map((todo) => (
                      <div key={todo.id} className={`graph-timeline-todo-item status-${todo.status}`}>
                        <span className="todo-status-icon" aria-hidden="true" />
                        <span className={todo.status === 'completed' ? 'todo-text-completed' : 'todo-text'}>
                          {todo.description}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}
