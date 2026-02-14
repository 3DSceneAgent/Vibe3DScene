import { useEffect, useRef, useState } from 'react'
import type { RenderImage } from '../api/types'
import type { SceneHierarchyNode } from '../state/types'
import { GltfViewer } from './GltfViewer'
import { RenderGallery } from './RenderGallery'
import { SceneInfoPanel } from './SceneInfoPanel'

type EnvironmentPreset = 'studio' | 'warm' | 'cool'

type SceneTabProps = {
  threadId: string
  renders: RenderImage[]
  gltfUrl: string | null
  sceneHierarchy: SceneHierarchyNode[]
  environment: EnvironmentPreset
  autoFetch: boolean
  onAutoFetchChange: (enabled: boolean) => void
  onEnvironmentChange: (preset: EnvironmentPreset) => void
  onFetchRenders: () => void
  onFetchGltf: () => void
  onDownloadGltf: () => void
  onDownloadBlend: () => void
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
        className="ghost-btn dropdown-trigger"
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
  gltfUrl,
  sceneHierarchy,
  environment,
  autoFetch,
  onAutoFetchChange,
  onEnvironmentChange,
  onFetchRenders,
  onFetchGltf,
  onDownloadGltf,
  onDownloadBlend,
  onHierarchyChange,
  loading,
  canRunActions
}: SceneTabProps) {
  const [objectsCollapsed, setObjectsCollapsed] = useState(false)
  const [isFullscreen, setIsFullscreen] = useState(false)

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
          onHierarchyChange={(hierarchy) => onHierarchyChange(threadId, hierarchy)}
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
          <button className="ghost-btn" onClick={onFetchRenders} disabled={loading.renders || !canRunActions}>
            Fetch Renders
          </button>
          <button className="primary-btn" onClick={onFetchGltf} disabled={loading.gltf || !canRunActions}>
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

      <RenderGallery renders={renders} isLoading={loading.renders} />

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
