type EnvironmentPreset = 'studio' | 'warm' | 'cool'

type TopBarProps = {
  environment: EnvironmentPreset
  onEnvironmentChange: (preset: EnvironmentPreset) => void
  onRefreshScene: () => void
  onFetchRenders: () => void
  onLoadGltf: () => void
  onDownloadGltf: () => void
  onOpenSettings: () => void
  isSceneLoading: boolean
  isRendersLoading: boolean
  isGltfLoading: boolean
  isDownloadLoading: boolean
  isDownloadDisabled: boolean
  canRunActions: boolean
}

export function TopBar({
  environment,
  onEnvironmentChange,
  onRefreshScene,
  onFetchRenders,
  onLoadGltf,
  onDownloadGltf,
  onOpenSettings,
  isSceneLoading,
  isRendersLoading,
  isGltfLoading,
  isDownloadLoading,
  isDownloadDisabled,
  canRunActions
}: TopBarProps) {
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
        <button className="ghost-btn" onClick={onOpenSettings}>
          Settings
        </button>
      </div>
    </div>
  )
}
