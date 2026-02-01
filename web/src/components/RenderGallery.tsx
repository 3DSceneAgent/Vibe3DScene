import type { RenderImage } from '../api/types'

type RenderGalleryProps = {
  renders: RenderImage[]
  isLoading: boolean
  onRefresh: () => void
}

export function RenderGallery({ renders, isLoading, onRefresh }: RenderGalleryProps) {
  return (
    <div className="panel">
      <div className="panel-header">
        <div className="panel-title">Camera Renders</div>
        <button className="ghost-btn" onClick={onRefresh} disabled={isLoading}>
          Fetch Renders
        </button>
      </div>
      {renders.length === 0 && <div className="muted">No renders loaded yet.</div>}
      <div className="render-grid">
        {renders.map((render) => (
          <div key={render.camera_name} className="render-card">
            <div className="render-title">{render.camera_name}</div>
            <img
              src={`data:image/png;base64,${render.image_base64}`}
              alt={`Render ${render.camera_name}`}
            />
          </div>
        ))}
      </div>
    </div>
  )
}
