import { useCallback, useEffect, useRef, useState } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from 'react'
import type { BlendFileEntry, RenderImage } from '../api/types'
import {
  environmentPresetOptions,
  environmentPresets,
  type EnvironmentPreset
} from '../constants/environmentPresets'
import type { SceneHierarchyNode } from '../state/types'
import { resolveMediaUrl } from '../utils/url'
import { GltfViewer } from './GltfViewer'
import { RenderGallery } from './RenderGallery'
import { SceneInfoPanel } from './SceneInfoPanel'

type ViewportTheme = 'auto' | 'dark' | 'light'
type UiTheme = 'dark' | 'light'

type SceneTabProps = {
  threadId: string
  renders: RenderImage[]
  backendUrl: string
  gltfUrl: string | null
  sceneHierarchy: SceneHierarchyNode[]
  environment: EnvironmentPreset
  environmentLightIntensity: number
  environmentBackgroundIntensity: number
  viewportTheme: ViewportTheme
  uiTheme: UiTheme
  showViewportGrid: boolean
  showHdriBackground: boolean
  onEnvironmentChange: (preset: EnvironmentPreset) => void
  onEnvironmentLightIntensityChange: (value: number) => void
  onEnvironmentBackgroundIntensityChange: (value: number) => void
  onFetchRenders: (includeLocalWork?: boolean) => void
  onFetchGltf: () => void
  onDownloadGltf: () => void
  onDownloadBlend: () => void
  onDownloadBlendFile: (relativePath: string, filename: string) => void
  onListBlendFiles: () => Promise<BlendFileEntry[]>
  actionError?: string | null
  onClearActionError?: () => void
  onHierarchyChange: (threadId: string, hierarchy: SceneHierarchyNode[]) => void
  loading: {
    scene: boolean
    renders: boolean
    gltf: boolean
    download: boolean
  }
  canRunActions: boolean
  idleActionHint?: string | null
  minimalUi?: boolean
}

type DownloadDropdownProps = {
  onDownloadGltf: () => void
  onDownloadBlend: () => void
  onDownloadBlendFile: (relativePath: string, filename: string) => void
  onListBlendFiles: () => Promise<BlendFileEntry[]>
  disabled: boolean
  loading: boolean
}

const DEFAULT_RENDER_PANEL_HEIGHT = 220
const MIN_RENDER_PANEL_HEIGHT = 140
const MIN_VIEWPORT_HEIGHT = 260
const LAYOUT_RESIZER_HEIGHT = 14
const KEYBOARD_RESIZE_STEP = 24

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max)
}

function getRenderLayoutBounds(totalHeight: number) {
  const usableHeight = Math.max(totalHeight - LAYOUT_RESIZER_HEIGHT, 0)
  if (usableHeight <= 0) {
    return {
      usableHeight: 0,
      minRenderHeight: 0,
      maxRenderHeight: 0
    }
  }

  let minRenderHeight = MIN_RENDER_PANEL_HEIGHT
  let minViewportHeight = MIN_VIEWPORT_HEIGHT
  const minimumRequiredHeight = minRenderHeight + minViewportHeight

  if (usableHeight < minimumRequiredHeight) {
    const scale = usableHeight / minimumRequiredHeight
    minRenderHeight = Math.max(96, Math.floor(minRenderHeight * scale))
    minViewportHeight = Math.max(160, Math.floor(minViewportHeight * scale))

    if (minRenderHeight + minViewportHeight > usableHeight) {
      minViewportHeight = Math.max(120, usableHeight - minRenderHeight)
    }

    if (minRenderHeight + minViewportHeight > usableHeight) {
      minRenderHeight = Math.max(80, usableHeight - minViewportHeight)
    }
  }

  minRenderHeight = Math.min(minRenderHeight, usableHeight)
  const maxRenderHeight = Math.min(Math.max(minRenderHeight, usableHeight - minViewportHeight), usableHeight)

  return {
    usableHeight,
    minRenderHeight,
    maxRenderHeight
  }
}

function formatBlendFileSize(sizeBytes: number): string {
  if (sizeBytes < 1024) return `${sizeBytes} B`
  if (sizeBytes < 1024 * 1024) return `${(sizeBytes / 1024).toFixed(1)} KB`
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`
}

function DownloadDropdown({
  onDownloadGltf,
  onDownloadBlend,
  onDownloadBlendFile,
  onListBlendFiles,
  disabled,
  loading
}: DownloadDropdownProps) {
  const [isOpen, setIsOpen] = useState(false)
  const [blendFiles, setBlendFiles] = useState<BlendFileEntry[]>([])
  const [isLoadingBlendFiles, setIsLoadingBlendFiles] = useState(false)
  const [blendFilesError, setBlendFilesError] = useState<string | null>(null)
  const dropdownRef = useRef<HTMLDivElement | null>(null)

  const loadBlendFiles = useCallback(async () => {
    setIsLoadingBlendFiles(true)
    setBlendFilesError(null)
    try {
      const files = await onListBlendFiles()
      setBlendFiles(files)
    } catch (error) {
      setBlendFiles([])
      setBlendFilesError(error instanceof Error ? error.message : 'Failed to load .blend files')
    } finally {
      setIsLoadingBlendFiles(false)
    }
  }, [onListBlendFiles])

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [])

  return (
    <div className="dropdown" ref={dropdownRef}>
      <button
        className="ghost-btn icon-btn dropdown-trigger"
        onClick={() =>
          setIsOpen((open) => {
            const next = !open
            if (next) {
              void loadBlendFiles()
            }
            return next
          })
        }
        disabled={disabled || loading}
        title="Download"
        aria-label="Download"
      >
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
          <path d="M8 1v10M4 8l4 4 4-4" />
          <path d="M2 13h12" />
        </svg>
      </button>
      {isOpen && (
        <div className="dropdown-menu">
          <button
            className="dropdown-item"
            onClick={() => {
              onDownloadGltf()
              setIsOpen(false)
            }}
            disabled={disabled || loading}
          >
            Download GLTF (.glb)
          </button>
          <button
            className="dropdown-item"
            onClick={() => {
              onDownloadBlend()
              setIsOpen(false)
            }}
            disabled={disabled || loading}
          >
            Download Current BLEND (.blend)
          </button>
          <div className="dropdown-section-label">Persisted BLEND Files</div>
          {isLoadingBlendFiles && <div className="dropdown-item dropdown-item-info">Loading...</div>}
          {!isLoadingBlendFiles && blendFilesError && (
            <button className="dropdown-item" onClick={() => void loadBlendFiles()} disabled={disabled || loading}>
              Retry loading files
            </button>
          )}
          {!isLoadingBlendFiles && !blendFilesError && blendFiles.length === 0 && (
            <div className="dropdown-item dropdown-item-info">No persisted .blend files</div>
          )}
          {!isLoadingBlendFiles &&
            !blendFilesError &&
            blendFiles.map((file) => (
              <button
                key={file.relative_path}
                className="dropdown-item dropdown-item-blend"
                onClick={() => {
                  onDownloadBlendFile(file.relative_path, file.filename)
                  setIsOpen(false)
                }}
                disabled={disabled || loading}
                title={file.relative_path}
              >
                <span>{file.filename}</span>
                <span className="dropdown-item-meta">
                  {file.category} · {formatBlendFileSize(file.size_bytes)}
                </span>
              </button>
            ))}
        </div>
      )}
    </div>
  )
}

export function SceneTab({
  threadId,
  renders,
  backendUrl,
  gltfUrl,
  sceneHierarchy,
  environment,
  environmentLightIntensity,
  environmentBackgroundIntensity,
  viewportTheme,
  uiTheme,
  showViewportGrid,
  showHdriBackground,
  onEnvironmentChange,
  onEnvironmentLightIntensityChange,
  onEnvironmentBackgroundIntensityChange,
  onFetchRenders,
  onFetchGltf,
  onDownloadGltf,
  onDownloadBlend,
  onDownloadBlendFile,
  onListBlendFiles,
  actionError,
  onClearActionError,
  onHierarchyChange,
  loading,
  canRunActions,
  idleActionHint = null,
  minimalUi = false
}: SceneTabProps) {
  const [objectsCollapsed, setObjectsCollapsed] = useState(true)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [alwaysAutoFrameCamera] = useState(false)
  const [twoSidedRendering, setTwoSidedRendering] = useState(false)
  const [wireframeOverlay, setWireframeOverlay] = useState(false)
  const [isEnvironmentMenuOpen, setIsEnvironmentMenuOpen] = useState(false)
  const [includeLocalWorkRenders, setIncludeLocalWorkRenders] = useState(false)
  const [renderPanelHeight, setRenderPanelHeight] = useState(DEFAULT_RENDER_PANEL_HEIGHT)
  const [layoutHeight, setLayoutHeight] = useState(0)
  const [isResizingLayout, setIsResizingLayout] = useState(false)
  const layoutRef = useRef<HTMLDivElement | null>(null)
  const environmentMenuRef = useRef<HTMLDivElement | null>(null)
  const showRenderGallery = !minimalUi
  const hasRenders = showRenderGallery && renders.length > 0
  const isSceneActionBusy = loading.scene || loading.renders || loading.gltf
  const fetchActionHint = !canRunActions ? idleActionHint : null
  const isHierarchyCollapsed = minimalUi || objectsCollapsed
  const resolvedGltfUrl = resolveMediaUrl(gltfUrl ?? undefined, backendUrl) ?? null
  const viewerKey = `${threadId}:${resolvedGltfUrl ?? 'empty'}`
  const handleHierarchyChange = useCallback(
    (hierarchy: SceneHierarchyNode[]) => onHierarchyChange(threadId, hierarchy),
    [onHierarchyChange, threadId]
  )

  useEffect(() => {
    if (!isFullscreen) return
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsFullscreen(false)
      }
    }
    window.addEventListener('keydown', handleEscape)
    return () => {
      window.removeEventListener('keydown', handleEscape)
    }
  }, [isFullscreen])

  useEffect(() => {
    if (!isEnvironmentMenuOpen) return

    const handlePointerDown = (event: MouseEvent) => {
      if (
        environmentMenuRef.current &&
        !environmentMenuRef.current.contains(event.target as Node)
      ) {
        setIsEnvironmentMenuOpen(false)
      }
    }

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsEnvironmentMenuOpen(false)
      }
    }

    document.addEventListener('mousedown', handlePointerDown)
    window.addEventListener('keydown', handleEscape)
    return () => {
      document.removeEventListener('mousedown', handlePointerDown)
      window.removeEventListener('keydown', handleEscape)
    }
  }, [isEnvironmentMenuOpen])

  useEffect(() => {
    const layoutElement = layoutRef.current
    if (!layoutElement) return

    const syncLayoutHeight = () => {
      setLayoutHeight(layoutElement.clientHeight)
    }

    syncLayoutHeight()
    const resizeObserver = new ResizeObserver(() => {
      syncLayoutHeight()
    })
    resizeObserver.observe(layoutElement)
    return () => {
      resizeObserver.disconnect()
    }
  }, [])

  useEffect(() => {
    document.body.classList.toggle('scene-layout-resizing', isResizingLayout)
    return () => {
      document.body.classList.remove('scene-layout-resizing')
    }
  }, [isResizingLayout])

  useEffect(() => {
    if (!isResizingLayout || !hasRenders) return

    const handlePointerMove = (event: PointerEvent) => {
      const layoutElement = layoutRef.current
      if (!layoutElement) return

      const { minRenderHeight, maxRenderHeight } = getRenderLayoutBounds(layoutElement.clientHeight)
      if (maxRenderHeight <= 0) return

      const layoutBounds = layoutElement.getBoundingClientRect()
      const nextHeight = clamp(event.clientY - layoutBounds.top, minRenderHeight, maxRenderHeight)
      setRenderPanelHeight(nextHeight)
    }

    const stopResizing = () => {
      setIsResizingLayout(false)
    }

    window.addEventListener('pointermove', handlePointerMove)
    window.addEventListener('pointerup', stopResizing)
    window.addEventListener('pointercancel', stopResizing)
    window.addEventListener('blur', stopResizing)
    return () => {
      window.removeEventListener('pointermove', handlePointerMove)
      window.removeEventListener('pointerup', stopResizing)
      window.removeEventListener('pointercancel', stopResizing)
      window.removeEventListener('blur', stopResizing)
    }
  }, [hasRenders, isResizingLayout])

  const handleLayoutResizeStart = (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (!hasRenders || event.button !== 0) return
    event.preventDefault()
    setIsResizingLayout(true)
  }

  const handleLayoutResizeKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    const { minRenderHeight, maxRenderHeight } = getRenderLayoutBounds(layoutRef.current?.clientHeight ?? layoutHeight)
    if (maxRenderHeight <= 0) return

    if (event.key === 'Home') {
      event.preventDefault()
      setRenderPanelHeight(minRenderHeight)
      return
    }

    if (event.key === 'End') {
      event.preventDefault()
      setRenderPanelHeight(maxRenderHeight)
      return
    }

    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return

    event.preventDefault()
    const delta = event.key === 'ArrowUp' ? -KEYBOARD_RESIZE_STEP : KEYBOARD_RESIZE_STEP
    setRenderPanelHeight((currentHeight) => clamp(currentHeight + delta, minRenderHeight, maxRenderHeight))
  }

  const { minRenderHeight, maxRenderHeight } = getRenderLayoutBounds(layoutHeight)
  const appliedRenderPanelHeight =
    hasRenders && maxRenderHeight > 0 ? clamp(renderPanelHeight, minRenderHeight, maxRenderHeight) : undefined

  const renderViewportControls = () => (
    <>
      <div
        ref={environmentMenuRef}
        className="viewer-environment-menu-anchor"
        title={environmentPresets[environment].description}
      >
        <button
          type="button"
          className={`ghost-btn viewer-environment-trigger ${isEnvironmentMenuOpen ? 'is-active' : ''}`}
          aria-haspopup="dialog"
          aria-expanded={isEnvironmentMenuOpen}
          onClick={() => setIsEnvironmentMenuOpen((open) => !open)}
        >
          <span className="viewer-environment-trigger-label">Light</span>
          <span className="viewer-environment-trigger-value">
            {environmentPresets[environment].label}
          </span>
          <svg
            className="viewer-environment-trigger-icon"
            width="12"
            height="12"
            viewBox="0 0 16 16"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <polyline points="4 6 8 10 12 6" />
          </svg>
        </button>
        {isEnvironmentMenuOpen && (
          <div className="viewer-environment-menu" role="dialog" aria-label="Viewport lighting controls">
            <div className="viewer-environment-menu-section">
              <label className="viewer-environment-menu-field" htmlFor={`viewer-environment-${threadId}`}>
                <span className="viewer-environment-label">Preset</span>
                <select
                  id={`viewer-environment-${threadId}`}
                  className="styled-select viewer-environment-select"
                  value={environment}
                  aria-label="Viewport environment"
                  onChange={(event) => onEnvironmentChange(event.target.value as EnvironmentPreset)}
                >
                  {environmentPresetOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {environment !== 'none' && (
              <div className="viewer-environment-menu-section">
                <div className="viewer-environment-intensity-control">
                  <label
                    className="viewer-environment-label"
                    htmlFor={`viewer-environment-intensity-${threadId}`}
                  >
                    Intensity
                  </label>
                  <input
                    id={`viewer-environment-intensity-${threadId}`}
                    className="viewer-environment-intensity-slider"
                    type="range"
                    min={35}
                    max={135}
                    step={1}
                    value={Math.round(clamp(environmentLightIntensity, 0.35, 1.35) * 100)}
                    aria-label="Environment lighting intensity"
                    onChange={(event) =>
                      onEnvironmentLightIntensityChange(Number(event.target.value) / 100)
                    }
                  />
                  <span className="viewer-environment-intensity-value" aria-hidden="true">
                    {Math.round(clamp(environmentLightIntensity, 0.35, 1.35) * 100)}%
                  </span>
                </div>
              </div>
            )}
            {environment !== 'none' && showHdriBackground && (
              <div className="viewer-environment-menu-section">
                <div className="viewer-environment-intensity-control">
                  <label
                    className="viewer-environment-label"
                    htmlFor={`viewer-environment-background-intensity-${threadId}`}
                  >
                    Bg
                  </label>
                  <input
                    id={`viewer-environment-background-intensity-${threadId}`}
                    className="viewer-environment-intensity-slider"
                    type="range"
                    min={15}
                    max={135}
                    step={1}
                    value={Math.round(clamp(environmentBackgroundIntensity, 0.15, 1.35) * 100)}
                    aria-label="Environment background intensity"
                    onChange={(event) =>
                      onEnvironmentBackgroundIntensityChange(Number(event.target.value) / 100)
                    }
                  />
                  <span className="viewer-environment-intensity-value" aria-hidden="true">
                    {Math.round(clamp(environmentBackgroundIntensity, 0.15, 1.35) * 100)}%
                  </span>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
      <span className="viewer-header-sep" aria-hidden="true" />
      <div className="viewer-control-group">
        <button
          type="button"
          className="ghost-btn icon-btn viewer-toolbar-btn"
          onClick={onFetchGltf}
          disabled={isSceneActionBusy || !canRunActions}
          title={fetchActionHint ?? 'Fetch scene'}
          aria-label="Fetch scene"
        >
          {loading.gltf ? (
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
        <DownloadDropdown
          onDownloadGltf={onDownloadGltf}
          onDownloadBlend={onDownloadBlend}
          onDownloadBlendFile={onDownloadBlendFile}
          onListBlendFiles={onListBlendFiles}
          disabled={!canRunActions}
          loading={loading.download}
        />
      </div>
      <span className="viewer-header-sep" aria-hidden="true" />
      <button
        type="button"
        className={`ghost-btn icon-btn viewer-toolbar-btn ${twoSidedRendering ? 'is-active' : ''}`}
        onClick={() => setTwoSidedRendering((v) => !v)}
        title={twoSidedRendering ? 'Two-sided rendering ON' : 'Two-sided rendering OFF'}
        aria-label="Toggle two-sided rendering"
        aria-pressed={twoSidedRendering}
      >
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
          <rect x="1" y="3" width="14" height="10" rx="1.5" />
          <line x1="8" y1="3" x2="8" y2="13" strokeDasharray="2 1.5" />
        </svg>
      </button>
      <button
        type="button"
        className={`ghost-btn icon-btn viewer-toolbar-btn ${wireframeOverlay ? 'is-active' : ''}`}
        onClick={() => setWireframeOverlay((value) => !value)}
        title={wireframeOverlay ? 'Wireframe overlay ON' : 'Wireframe overlay OFF'}
        aria-label="Toggle wireframe overlay"
        aria-pressed={wireframeOverlay}
      >
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.35" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 4.5 8 2l5 2.5v7L8 14l-5-2.5z" />
          <path d="M3 4.5 8 7l5-2.5M8 7v7" />
          <path d="M5.2 8.2 10.8 11M10.8 8.2 5.2 11" opacity="0.9" />
        </svg>
      </button>
    </>
  )

  const coreLayout = (fullscreen: boolean) => (
    <div className={`scene-core ${isHierarchyCollapsed ? 'objects-collapsed' : ''}`}>
      <div className="scene-core-viewport">
        <GltfViewer
          key={viewerKey}
          gltfUrl={resolvedGltfUrl}
          environment={environment}
          environmentLightIntensity={environmentLightIntensity}
          environmentBackgroundIntensity={environmentBackgroundIntensity}
          viewportTheme={viewportTheme}
          uiTheme={uiTheme}
          showGrid={showViewportGrid}
          showHdriBackground={showHdriBackground}
          twoSidedRendering={twoSidedRendering}
          showWireframeOverlay={wireframeOverlay}
          alwaysAutoFrameCamera={alwaysAutoFrameCamera}
          onHierarchyChange={handleHierarchyChange}
          isFullscreen={fullscreen}
          onToggleFullscreen={() => setIsFullscreen((value) => !value)}
          showFullscreenButton={!minimalUi}
          headerTrailingControls={
            !minimalUi && objectsCollapsed ? (
              <button
                className="ghost-btn icon-btn viewer-toolbar-btn"
                onClick={() => setObjectsCollapsed(false)}
                title="Show hierarchy"
                aria-label="Show hierarchy"
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="1" y="1" width="5" height="4" rx="1" />
                  <rect x="10" y="1" width="5" height="4" rx="1" />
                  <rect x="10" y="11" width="5" height="4" rx="1" />
                  <path d="M3.5 5v3h9M12.5 8v3" />
                </svg>
              </button>
            ) : null
          }
          headerControls={renderViewportControls()}
        />
      </div>
      {!minimalUi && !objectsCollapsed && (
        <SceneInfoPanel
          hierarchy={sceneHierarchy}
          collapsed={false}
          onToggleCollapse={() => setObjectsCollapsed((value) => !value)}
          isSceneSyncing={loading.scene}
        />
      )}
    </div>
  )

  return (
    <div className={`scene-tab scene-pane ${minimalUi ? 'minimal-ui' : ''}`}>
      {actionError && (
        <div className="scene-action-error" role="alert">
          <span>{actionError}</span>
          {onClearActionError && (
            <button className="text-btn" type="button" onClick={onClearActionError}>
              Dismiss
            </button>
          )}
        </div>
      )}

      <div
        ref={layoutRef}
        className={`scene-layout-split ${hasRenders ? 'has-renders' : 'is-empty'} ${isResizingLayout ? 'is-resizing' : ''}`}
      >
        {showRenderGallery && (
          <RenderGallery
            renders={renders}
            isLoading={loading.renders}
            backendUrl={backendUrl}
            includeLocalWork={includeLocalWorkRenders}
            onIncludeLocalWorkChange={setIncludeLocalWorkRenders}
            onFetchRenders={() => onFetchRenders(includeLocalWorkRenders)}
            fetchDisabled={isSceneActionBusy || !canRunActions}
            style={appliedRenderPanelHeight ? { height: `${appliedRenderPanelHeight}px` } : undefined}
          />
        )}

        {hasRenders && (
          <button
            type="button"
            className="scene-layout-resizer"
            onPointerDown={handleLayoutResizeStart}
            onKeyDown={handleLayoutResizeKeyDown}
            aria-label="Resize camera renders and 3D viewport"
            aria-orientation="horizontal"
            aria-valuemin={Math.round(minRenderHeight)}
            aria-valuemax={Math.round(maxRenderHeight)}
            aria-valuenow={Math.round(appliedRenderPanelHeight ?? minRenderHeight)}
            role="separator"
            title="Drag to resize camera renders and 3D viewport"
          />
        )}

        <div className="scene-core-shell">{coreLayout(false)}</div>
      </div>

      {isFullscreen && (
        <div
          className="scene-fullscreen-overlay"
          onClick={(event) => {
            if (event.target === event.currentTarget) {
              setIsFullscreen(false)
            }
          }}
        >
          <div className="scene-fullscreen-modal">{coreLayout(true)}</div>
        </div>
      )}
    </div>
  )
}
