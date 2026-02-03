type EnvironmentPreset = 'studio' | 'warm' | 'cool'
type BackendStatus = 'online' | 'offline' | 'checking'

type TopBarProps = {
  environment: EnvironmentPreset
  onEnvironmentChange: (preset: EnvironmentPreset) => void
  onRefreshScene: () => void
  onFetchRenders: () => void
  onLoadGltf: () => void
  onDownloadGltf: () => void
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
  backendUrl: string
}

export function TopBar({
  environment,
  onEnvironmentChange,
  onRefreshScene,
  onFetchRenders,
  onLoadGltf,
  onDownloadGltf,
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
  backendUrl
}: TopBarProps) {
  const statusLabel =
    backendStatus === 'online' ? 'Online' : backendStatus === 'offline' ? 'Offline' : 'Checking'

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
            <span className="status-text">Server {statusLabel}</span>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="top-bar">
      <div className="top-bar-group">
        <button className="ghost-btn" onClick={onRefreshScene} disabled={isSceneLoading || !canRunActions}>
          Refresh Scene
        </button>
        <button className="ghost-btn" onClick={onFetchRenders} disabled={isRendersLoading || !canRunActions}>
          Fetch Renders
        </button>
        <button className="primary-btn" onClick={onLoadGltf} disabled={isGltfLoading || !canRunActions}>
          Load 3D Scene
        </button>
        <button
          className="ghost-btn"
          onClick={onDownloadGltf}
          disabled={isDownloadLoading || isDownloadDisabled || !canRunActions}
        >
          Download GLTF
        </button>
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
          <span className="status-text">Server {statusLabel}</span>
        </div>
        <button className="ghost-btn" onClick={onOpenSettings}>
          Settings
        </button>
      </div>
    </div>
  )
}
