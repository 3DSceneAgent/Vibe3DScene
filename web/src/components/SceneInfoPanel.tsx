import type { SceneInfo } from '../api/types'

type SceneInfoPanelProps = {
  scene: SceneInfo | null
  isLoading: boolean
  onRefresh: () => void
}

export function SceneInfoPanel({ scene, isLoading, onRefresh }: SceneInfoPanelProps) {
  const objects = scene?.scene_objects ?? {}

  return (
    <div className="panel">
      <div className="panel-header">
        <div className="panel-title">Scene Objects</div>
        <button className="ghost-btn" onClick={onRefresh} disabled={isLoading}>
          Refresh
        </button>
      </div>
      {Object.keys(objects).length === 0 && (
        <div className="muted">No scene information yet.</div>
      )}
      <div className="scene-object-list">
        {Object.entries(objects).map(([name, info]) => {
          const dimensions = Array.isArray(info.dimensions)
            ? info.dimensions.map((value) => (typeof value === 'number' ? value.toFixed(2) : '--')).join(', ')
            : '--'
          return (
          <div key={name} className="scene-object-item">
            <div className="scene-object-name">{name}</div>
            <div className="scene-object-meta">
              {info.type || 'Unknown'} · {dimensions}
            </div>
          </div>
          )
        })}
      </div>
    </div>
  )
}
