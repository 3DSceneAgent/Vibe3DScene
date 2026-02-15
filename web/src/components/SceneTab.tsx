import { useCallback, useEffect, useRef, useState } from 'react'
import type { RenderImage } from '../api/types'
import type { SceneHierarchyNode } from '../state/types'
import { GltfViewer } from './GltfViewer'
import { RenderGallery } from './RenderGallery'
import { SceneInfoPanel } from './SceneInfoPanel'

type EnvironmentPreset = 'studio' | 'warm' | 'cool'
type ViewportTheme = 'auto' | 'dark' | 'light'
type UiTheme = 'dark' | 'light'

type SceneTabProps = {
  threadId: string
  renders: RenderImage[]
  backendUrl: string
  gltfUrl: string | null
  sceneHierarchy: SceneHierarchyNode[]
  environment: EnvironmentPreset
  viewportTheme: ViewportTheme
  uiTheme: UiTheme
  autoFetch: boolean
  onAutoFetchChange: (enabled: boolean) => void
  onEnvironmentChange: (preset: EnvironmentPreset) => void
  onFetchRenders: () => void
  onFetchGltf: () => void
  onDownloadGltf: () => void
  onDownloadBlend: () => void
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
}

type DownloadDropdownProps = {
  onDownloadGltf: () => void
  onDownloadBlend: () => void
  disabled: boolean
  loading: boolean
}

function DownloadDropdown({
  onDownloadGltf,
  onDownloadBlend,
  disabled,
  loading
}: DownloadDropdownProps) {
  const [isOpen, setIsOpen] = useState(false)
  const dropdownRef = useRef<HTMLDivElement | null>(null)

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
        className="primary-btn dropdown-trigger"
        onClick={() => setIsOpen((open) => !open)}
        disabled={disabled || loading}
      >
        Download 
      </button>
      {isOpen && (
        <div className="dropdown-menu">
          <button className="dropdown-item" onClick={onDownloadGltf} disabled={disabled || loading}>
            Download GLTF (.glb)
          </button>
          <button className="dropdown-item" onClick={onDownloadBlend} disabled={disabled || loading}>
            Download BLEND (.blend)
          </button>
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
  viewportTheme,
  uiTheme,
  autoFetch,
  onAutoFetchChange,
  onEnvironmentChange,
  onFetchRenders,
  onFetchGltf,
  onDownloadGltf,
  onDownloadBlend,
  actionError,
  onClearActionError,
  onHierarchyChange,
  loading,
  canRunActions
}: SceneTabProps) {
  const [objectsCollapsed, setObjectsCollapsed] = useState(true)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const isSceneActionBusy = loading.scene || loading.renders || loading.gltf
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

  const coreLayout = (fullscreen: boolean) => (
    <div className={`scene-core ${objectsCollapsed ? 'objects-collapsed' : ''}`}>
      <div className="scene-core-viewport">
        <GltfViewer
          gltfUrl={gltfUrl}
          environment={environment}
          viewportTheme={viewportTheme}
          uiTheme={uiTheme}
          onHierarchyChange={handleHierarchyChange}
          isFullscreen={fullscreen}
          onToggleFullscreen={() => setIsFullscreen((value) => !value)}
        />
      </div>
      <SceneInfoPanel
        hierarchy={sceneHierarchy}
        collapsed={objectsCollapsed}
        onToggleCollapse={() => setObjectsCollapsed((value) => !value)}
        isSceneSyncing={loading.scene}
      />
    </div>
  )

  return (
    <div className="scene-tab scene-pane">
      <div className="scene-action-bar">
        <div className="scene-actions-left">
          <button className="primary-btn" onClick={onFetchRenders} disabled={isSceneActionBusy || !canRunActions}>
            Fetch Renders
          </button>
          <button className="primary-btn" onClick={onFetchGltf} disabled={isSceneActionBusy || !canRunActions}>
            Fetch Scene
          </button>
          <DownloadDropdown
            onDownloadGltf={onDownloadGltf}
            onDownloadBlend={onDownloadBlend}
            disabled={!canRunActions}
            loading={loading.download}
          />
        </div>
        <div className="scene-actions-right">
          <label className="toggle-switch">
            <input
              type="checkbox"
              checked={autoFetch}
              onChange={(event) => onAutoFetchChange(event.target.checked)}
            />
            <span className="toggle-slider" />
            <span className="toggle-label">Auto-fetch</span>
          </label>
          <label className="select-label">
            EnvLight
            <select
              className="styled-select"
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

      <RenderGallery renders={renders} isLoading={loading.renders} backendUrl={backendUrl} />

      <div className="scene-core-shell">{coreLayout(false)}</div>

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
