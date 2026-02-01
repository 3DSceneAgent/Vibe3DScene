import type { TodoItem } from '../api/types'

type TodosPanelProps = {
  todos: TodoItem[]
  isLoading: boolean
  onRefresh: () => void
}

const statusLabels: Record<string, string> = {
  pending: 'Pending',
  in_progress: 'In Progress',
  completed: 'Completed',
  failed: 'Failed'
}

export function TodosPanel({ todos, isLoading, onRefresh }: TodosPanelProps) {
  return (
    <div className="panel">
      <div className="panel-header">
        <div className="panel-title">Todos</div>
        <button className="ghost-btn" onClick={onRefresh} disabled={isLoading}>
          Refresh
        </button>
      </div>
      {todos.length === 0 && <div className="muted">No todos yet.</div>}
      <div className="todo-list">
        {todos.map((todo) => (
          <div key={todo.id} className={`todo-item status-${todo.status}`}>
            <span className="todo-status">{statusLabels[todo.status] ?? todo.status}</span>
            <span className="todo-text">{todo.description}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
