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
}

export function SceneTab({
  scene,
  renders,
  gltfUrl,
  environment,
  loading
}: SceneTabProps) {
  return (
    <div className="scene-tab scene-pane">
      <div className="scene-grid">
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
