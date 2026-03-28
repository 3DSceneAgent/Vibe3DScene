import { useEffect, useState, type CSSProperties } from 'react'
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

function getRenderKey(render: RenderImage): string {
  return `${render.camera_name}:${render.image_url}`
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
  const [selectedRenderKey, setSelectedRenderKey] = useState<string | null>(null)
  const selectedRender =
    selectedRenderKey == null
      ? null
      : renders.find((render) => getRenderKey(render) === selectedRenderKey) ?? null

  useEffect(() => {
    if (!selectedRender) return

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setSelectedRenderKey(null)
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => {
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [selectedRender])

  return (
    <>
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
              {renders.map((render) => {
                const imageUrl = resolveMediaUrl(render.image_url, backendUrl)

                return (
                  <div key={render.camera_name} className="render-card">
                    <div className="render-title">{render.camera_name}</div>
                    <div className="render-image-frame">
                      <button
                        type="button"
                        className="render-image-trigger"
                        onClick={() => setSelectedRenderKey(getRenderKey(render))}
                        title={`Open ${render.camera_name}`}
                        aria-label={`Open ${render.camera_name}`}
                      >
                        <img src={imageUrl} alt={`Render ${render.camera_name}`} />
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )}
        {hasRenders && isLoading && <div className="muted">Fetching renders...</div>}
      </div>

      {selectedRender && (
        <div
          className="render-preview-overlay"
          role="presentation"
          onClick={(event) => {
            if (event.target === event.currentTarget) {
              setSelectedRenderKey(null)
            }
          }}
        >
          <div
            className="render-preview-modal"
            role="dialog"
            aria-modal="true"
            aria-label={`Render preview ${selectedRender.camera_name}`}
          >
            <div className="render-preview-header">
              <div className="panel-title">{selectedRender.camera_name}</div>
              <button
                type="button"
                className="ghost-btn icon-btn viewer-toolbar-btn render-preview-close"
                onClick={() => setSelectedRenderKey(null)}
                title="Close preview"
                aria-label="Close preview"
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
                  <path d="M3 3l10 10M13 3L3 13" />
                </svg>
              </button>
            </div>
            <div className="render-preview-frame">
              <img
                className="render-preview-image"
                src={resolveMediaUrl(selectedRender.image_url, backendUrl)}
                alt={`Render ${selectedRender.camera_name}`}
              />
            </div>
          </div>
        </div>
      )}
    </>
  )
}
