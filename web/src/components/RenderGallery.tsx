import type { CSSProperties } from 'react'
import type { RenderImage } from '../api/types'
import { resolveMediaUrl } from '../utils/url'

type RenderGalleryProps = {
  renders: RenderImage[]
  isLoading: boolean
  backendUrl: string
  includeLocalWork: boolean
  onIncludeLocalWorkChange: (enabled: boolean) => void
  onFetchRenders?: () => void
  fetchDisabled?: boolean
  style?: CSSProperties
}

export function RenderGallery({
  renders,
  isLoading,
  backendUrl,
  includeLocalWork,
  onIncludeLocalWorkChange,
  onFetchRenders,
  fetchDisabled = false,
  style
}: RenderGalleryProps) {
  const hasRenders = renders.length > 0

  return (
    <div className={`scene-renders-panel ${hasRenders ? 'has-renders' : 'is-empty'}`} style={style}>
      <div className="panel-header">
        <div className="panel-title">Camera Renders</div>
        <div className="panel-header-actions">
          <button
            type="button"
            className={`ghost-btn icon-btn viewer-toolbar-btn ${includeLocalWork ? 'is-active' : ''}`}
            onClick={() => onIncludeLocalWorkChange(!includeLocalWork)}
            title={includeLocalWork ? 'Local camera: ON' : 'Local camera: OFF'}
            aria-label="Toggle local camera"
            aria-pressed={includeLocalWork}
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
              <path d="M1 3.5h10v8H1z" />
              <path d="M11 6l4-2v8l-4-2" />
            </svg>
          </button>
          {onFetchRenders && (
            <button
              type="button"
              className="ghost-btn icon-btn viewer-toolbar-btn"
              onClick={onFetchRenders}
              disabled={fetchDisabled || isLoading}
              title="Fetch renders"
              aria-label="Fetch renders"
            >
              {isLoading ? (
                <svg className="spin" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
                  <path d="M14 8a6 6 0 1 1-1.5-4" />
                  <polyline points="14 2 14 5.5 10.5 5.5" />
                </svg>
              ) : (
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
                  <path d="M14 8a6 6 0 1 1-1.5-4" />
                  <polyline points="14 2 14 5.5 10.5 5.5" />
                </svg>
              )}
            </button>
          )}
        </div>
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
