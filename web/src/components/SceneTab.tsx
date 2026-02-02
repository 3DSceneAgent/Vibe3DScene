import type { RenderImage, SceneInfo, TodoItem } from '../api/types'
import { GltfViewer } from './GltfViewer'
import { RenderGallery } from './RenderGallery'
import { SceneInfoPanel } from './SceneInfoPanel'
import { TodosPanel } from './TodosPanel'

type EnvironmentPreset = 'studio' | 'warm' | 'cool'

type SceneTabProps = {
  scene: SceneInfo | null
  todos: TodoItem[]
  renders: RenderImage[]
  gltfUrl: string | null
  environment: EnvironmentPreset
  onEnvironmentChange: (preset: EnvironmentPreset) => void
  onRefreshScene: () => void
  onRefreshTodos: () => void
  onFetchRenders: () => void
  onFetchGltf: () => void
  loading: {
    scene: boolean
    todos: boolean
    renders: boolean
    gltf: boolean
  }
}

export function SceneTab({
  scene,
  todos,
  renders,
  gltfUrl,
  environment,
  onEnvironmentChange,
  onRefreshScene,
  onRefreshTodos,
  onFetchRenders,
  onFetchGltf,
  loading
}: SceneTabProps) {
  return (
    <div className="scene-tab scene-pane">
      <div className="scene-toolbar">
        <div className="toolbar-group">
          <button className="ghost-btn" onClick={onRefreshScene} disabled={loading.scene}>
            Refresh Scene
          </button>
          <button className="ghost-btn" onClick={onRefreshTodos} disabled={loading.todos}>
            Refresh Todos
          </button>
          <button className="ghost-btn" onClick={onFetchRenders} disabled={loading.renders}>
            Fetch Renders
          </button>
          <button className="primary-btn" onClick={onFetchGltf} disabled={loading.gltf}>
            Load 3D Scene
          </button>
        </div>
        <div className="toolbar-group">
          <label className="select-label">
            Environment
            <select
              value={environment}
              onChange={(event) => onEnvironmentChange(event.target.value as EnvironmentPreset)}
            >
              <option value="studio">Studio</option>
              <option value="warm">Warm</option>
              <option value="cool">Cool</option>
            </select>
          </label>
        </div>
      </div>

      <div className="scene-scroll-area">
        <div className="scene-grid">
          <div className="scene-top">
            <div className="scene-left">
              <SceneInfoPanel scene={scene} isLoading={loading.scene} onRefresh={onRefreshScene} />
              <TodosPanel todos={todos} isLoading={loading.todos} onRefresh={onRefreshTodos} />
            </div>
            <div className="scene-right">
              <RenderGallery renders={renders} isLoading={loading.renders} onRefresh={onFetchRenders} />
            </div>
          </div>
          <div className="scene-viewer">
            <GltfViewer gltfUrl={gltfUrl} environment={environment} />
          </div>
        </div>
      </div>
    </div>
  )
}
