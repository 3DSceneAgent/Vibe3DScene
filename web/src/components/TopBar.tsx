import { useState, useRef, useEffect } from 'react'

type EnvironmentPreset = 'studio' | 'warm' | 'cool'
type BackendStatus = 'online' | 'offline' | 'checking'
type BackendMode = 'headless' | 'local-client' | null

type TopBarProps = {
  environment: EnvironmentPreset
  onEnvironmentChange: (preset: EnvironmentPreset) => void
  onRefreshScene: () => void
  onFetchRenders: () => void
  onLoadGltf: () => void
  onDownloadGltf: () => void
  onDownloadBlend: () => void
  autoRefreshScene: boolean
  onAutoRefreshChange: (enabled: boolean) => void
  sceneCollapsed: boolean
  onSceneToggle: () => void
  onOpenSettings: () => void
  isSceneLoading: boolean
  isRendersLoading: boolean
  isGltfLoading: boolean
  isDownloadLoading: boolean
  isDownloadDisabled: boolean
  canRunActions: boolean
  backendStatus: BackendStatus
  backendMode: BackendMode
  backendUrl: string
}

function DownloadDropdown({
  onDownloadGltf,
  onDownloadBlend,
  isDownloadLoading,
  isDownloadDisabled,
  canRunActions
}: {
  onDownloadGltf: () => void
  onDownloadBlend: () => void
  isDownloadLoading: boolean
  isDownloadDisabled: boolean
  canRunActions: boolean
}) {
  const [isOpen, setIsOpen] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const handleDownload = (format: 'gltf' | 'blend') => {
    setIsOpen(false)
    if (format === 'gltf') {
      onDownloadGltf()
    } else {
      onDownloadBlend()
    }
  }

  return (
    <div className="dropdown" ref={dropdownRef}>
      <button
        className="ghost-btn dropdown-trigger"
        onClick={() => setIsOpen(!isOpen)}
        disabled={isDownloadLoading || isDownloadDisabled || !canRunActions}
      >
        Export {isOpen ? '▲' : '▼'}
      </button>
      {isOpen && (
        <div className="dropdown-menu">
          <button
            className="dropdown-item"
            onClick={() => handleDownload('gltf')}
            disabled={isDownloadLoading || isDownloadDisabled || !canRunActions}
          >
            Download as GLTF (.glb)
          </button>
          <button
            className="dropdown-item"
            onClick={() => handleDownload('blend')}
            disabled={isDownloadLoading || isDownloadDisabled || !canRunActions}
          >
            Download as BLEND (.blend)
          </button>
        </div>
      )}
    </div>
  )
}

export function TopBar({
  environment,
  onEnvironmentChange,
  onRefreshScene,
  onFetchRenders,
  onLoadGltf,
  onDownloadGltf,
  onDownloadBlend,
  autoRefreshScene,
  onAutoRefreshChange,
  sceneCollapsed,
  onSceneToggle,
  onOpenSettings,
  isSceneLoading,
  isRendersLoading,
  isGltfLoading,
  isDownloadLoading,
  isDownloadDisabled,
  canRunActions,
  backendStatus,
  backendMode,
  backendUrl
}: TopBarProps) {
  const statusLabel =
    backendStatus === 'online' ? 'Online' : backendStatus === 'offline' ? 'Offline' : 'Checking'
  const modeLabel =
    backendMode === 'headless' ? 'Headless' : backendMode === 'local-client' ? 'Local' : null
  const statusText = modeLabel ? `Server ${statusLabel} • ${modeLabel}` : `Server ${statusLabel}`

  if (sceneCollapsed) {
    return (
      <div className="top-bar">
        <div className="top-bar-group">
          <button className="ghost-btn" onClick={onSceneToggle} disabled={!canRunActions}>
            Show Scene
          </button>
        </div>
        <div className="top-bar-group">
          <div className="top-bar-status" title={`Backend: ${backendUrl}`}>
            <span className={`status-dot ${backendStatus}`} />
            <span className="status-text">{statusText}</span>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="top-bar">
      <div className="top-bar-group">
        <button className="ghost-btn" onClick={() => onRefreshScene()} disabled={isSceneLoading || !canRunActions}>
          Refresh Scene
        </button>
        <button className="ghost-btn" onClick={() => onFetchRenders()} disabled={isRendersLoading || !canRunActions}>
          Fetch Renders
        </button>
        <button className="primary-btn" onClick={() => onLoadGltf()} disabled={isGltfLoading || !canRunActions}>
          Load 3D Scene
        </button>
        <DownloadDropdown
          onDownloadGltf={onDownloadGltf}
          onDownloadBlend={onDownloadBlend}
          isDownloadLoading={isDownloadLoading}
          isDownloadDisabled={isDownloadDisabled}
          canRunActions={canRunActions}
        />
        <label className="toggle-switch">
          <input
            type="checkbox"
            checked={autoRefreshScene}
            onChange={(event) => onAutoRefreshChange(event.target.checked)}
          />
          <span className="toggle-slider" />
          <span className="toggle-label">Auto-fetch</span>
        </label>
        <button className="ghost-btn" onClick={onSceneToggle} disabled={!canRunActions}>
          {sceneCollapsed ? 'Show Scene' : 'Hide Scene'}
        </button>
      </div>
      <div className="top-bar-group">
        <label className="select-label">
          Environment
          <select
            value={environment}
            onChange={(event) => onEnvironmentChange(event.target.value as EnvironmentPreset)}
          >
            <option value="studio">Studio</option>
            <option value="warm">Warm</option>
            <option value="cool">Cool</option>
          </select>
        </label>
        <div className="top-bar-status" title={`Backend: ${backendUrl}`}>
          <span className={`status-dot ${backendStatus}`} />
          <span className="status-text">{statusText}</span>
        </div>
        <button className="ghost-btn" onClick={onOpenSettings}>
          Settings
        </button>
      </div>
    </div>
  )
}
