import type { RenderImage } from '../api/types'

type RenderGalleryProps = {
  renders: RenderImage[]
  isLoading: boolean
}

export function RenderGallery({ renders, isLoading }: RenderGalleryProps) {
  return (
    <div className="scene-renders-panel">
      <div className="panel-header">
        <div className="panel-title">Camera Renders</div>
      </div>
      {renders.length === 0 && <div className="muted">No renders loaded yet.</div>}
      <div className="render-scroll">
        <div className="render-grid">
          {renders.map((render) => (
            <div key={render.camera_name} className="render-card">
              <div className="render-title">{render.camera_name}</div>
              <div className="render-image-frame">
                <img src={`data:image/png;base64,${render.image_base64}`} alt={`Render ${render.camera_name}`} />
              </div>
            </div>
          ))}
        </div>
      </div>
      {isLoading && <div className="muted">Fetching renders...</div>}
    </div>
  )
}
