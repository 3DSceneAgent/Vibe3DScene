import type { RenderImage } from '../api/types'
import { resolveMediaUrl } from '../utils/url'

type RenderGalleryProps = {
  renders: RenderImage[]
  isLoading: boolean
  backendUrl: string
}

export function RenderGallery({ renders, isLoading, backendUrl }: RenderGalleryProps) {
  const hasRenders = renders.length > 0

  return (
    <div className={`scene-renders-panel ${hasRenders ? 'has-renders' : 'is-empty'}`}>
      <div className="panel-header">
        <div className="panel-title">Camera Renders</div>
      </div>
      {!hasRenders && <div className="muted">{isLoading ? 'Fetching renders...' : 'No renders loaded yet.'}</div>}
      {hasRenders && (
        <div className="render-scroll">
          <div className="render-grid">
            {renders.map((render) => (
              <div key={render.camera_name} className="render-card">
                <div className="render-title">{render.camera_name}</div>
                <div className="render-image-frame">
                  <img
                    src={resolveMediaUrl(render.image_url, backendUrl)}
                    alt={`Render ${render.camera_name}`}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
      {hasRenders && isLoading && <div className="muted">Fetching renders...</div>}
    </div>
  )
}
