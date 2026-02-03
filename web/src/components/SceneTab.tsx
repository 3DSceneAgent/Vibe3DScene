import type { RenderImage, SceneInfo } from '../api/types'
import { GltfViewer } from './GltfViewer'
import { RenderGallery } from './RenderGallery'
import { SceneInfoPanel } from './SceneInfoPanel'

type EnvironmentPreset = 'studio' | 'warm' | 'cool'

type SceneTabProps = {
  scene: SceneInfo | null
  renders: RenderImage[]
  gltfUrl: string | null
  environment: EnvironmentPreset
  loading: {
    scene: boolean
    renders: boolean
  }
  collapsed: boolean
  onToggleCollapse: () => void
}

export function SceneTab({
  scene,
  renders,
  gltfUrl,
  environment,
  loading,
  collapsed,
  onToggleCollapse
}: SceneTabProps) {
  return (
    <div className="scene-tab scene-pane">
      <div className="scene-toolbar">
        <div className="panel-title">Scene</div>
        {/* <button className="text-btn scene-collapse-toggle" onClick={onToggleCollapse}>
          {collapsed ? 'Show scene' : 'Hide scene'}
        </button> */}
      </div>
      <div className={`scene-grid ${collapsed ? 'is-collapsed' : ''}`}>
        <div className="scene-top">
          <div className="scene-left">
            <SceneInfoPanel scene={scene} isLoading={loading.scene} />
          </div>
          <div className="scene-right">
            <RenderGallery renders={renders} isLoading={loading.renders} />
          </div>
        </div>
        <div className="scene-viewer">
          <GltfViewer gltfUrl={gltfUrl} environment={environment} />
        </div>
      </div>
    </div>
  )
}
