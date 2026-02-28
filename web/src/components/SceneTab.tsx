import { useCallback, useEffect, useRef, useState, type ChangeEvent } from 'react'
import type { BlendFileEntry, RenderImage } from '../api/types'
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
  onFetchRenders: (includeLocalWork?: boolean) => void
  onFetchGltf: () => void
  onDebugUploadGltf: (file: File) => void
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
}

type DownloadDropdownProps = {
  onDownloadGltf: () => void
  onDownloadBlend: () => void
  onDownloadBlendFile: (relativePath: string, filename: string) => void
  onListBlendFiles: () => Promise<BlendFileEntry[]>
  disabled: boolean
  loading: boolean
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
        className="primary-btn dropdown-trigger"
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
      >
        Download
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
  viewportTheme,
  uiTheme,
  autoFetch,
  onAutoFetchChange,
  onEnvironmentChange,
  onFetchRenders,
  onFetchGltf,
  onDebugUploadGltf,
  onDownloadGltf,
  onDownloadBlend,
  onDownloadBlendFile,
  onListBlendFiles,
  actionError,
  onClearActionError,
  onHierarchyChange,
  loading,
  canRunActions,
  idleActionHint = null
}: SceneTabProps) {
  const [objectsCollapsed, setObjectsCollapsed] = useState(true)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [alwaysAutoFrameCamera, setAlwaysAutoFrameCamera] = useState(false)
  const [includeLocalWorkRenders, setIncludeLocalWorkRenders] = useState(false)
  const debugFileInputRef = useRef<HTMLInputElement | null>(null)
  const isSceneActionBusy = loading.scene || loading.renders || loading.gltf
  const fetchActionHint = !canRunActions ? idleActionHint : null
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

  const handleDebugUploadClick = useCallback(() => {
    debugFileInputRef.current?.click()
  }, [])

  const handleDebugFileChange = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0]
      if (file) {
        onDebugUploadGltf(file)
      }
      // Allow selecting the same file again in subsequent debug attempts.
      event.currentTarget.value = ''
    },
    [onDebugUploadGltf]
  )

  const coreLayout = (fullscreen: boolean) => (
    <div className={`scene-core ${objectsCollapsed ? 'objects-collapsed' : ''}`}>
      <div className="scene-core-viewport">
        <GltfViewer
          gltfUrl={gltfUrl}
          environment={environment}
          viewportTheme={viewportTheme}
          uiTheme={uiTheme}
          alwaysAutoFrameCamera={alwaysAutoFrameCamera}
          onHierarchyChange={handleHierarchyChange}
          isFullscreen={fullscreen}
          onToggleFullscreen={() => setIsFullscreen((value) => !value)}
          headerTrailingControls={
            objectsCollapsed ? (
              <button className="ghost-btn viewer-show-hier-btn" onClick={() => setObjectsCollapsed(false)}>
                Show Hier
              </button>
            ) : null
          }
          headerControls={
            <>
              <label className="toggle-switch">
                <input
                  type="checkbox"
                  checked={alwaysAutoFrameCamera}
                  onChange={(event) => setAlwaysAutoFrameCamera(event.target.checked)}
                />
                {/* <span className="toggle-slider" /> */}
                {/* <span className="toggle-label">AutoCamera</span> */}
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
              <input
                ref={debugFileInputRef}
                className="viewer-debug-file-input"
                type="file"
                accept=".glb,.gltf,model/gltf-binary,model/gltf+json"
                onChange={handleDebugFileChange}
              />
              <button
                className="ghost-btn viewer-debug-upload-btn"
                type="button"
                onClick={handleDebugUploadClick}
                title="Upload local GLTF/GLB for viewport debug"
              >
                Debug Upload
              </button>
              {fullscreen && (
                <DownloadDropdown
                  onDownloadGltf={onDownloadGltf}
                  onDownloadBlend={onDownloadBlend}
                  onDownloadBlendFile={onDownloadBlendFile}
                  onListBlendFiles={onListBlendFiles}
                  disabled={!canRunActions}
                  loading={loading.download}
                />
              )}
            </>
          }
        />
      </div>
      {!objectsCollapsed && (
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
    <div className="scene-tab scene-pane">
      <div className="scene-action-bar">
        <div className="scene-actions-left">
          <span className="scene-action-with-hint" data-hint={fetchActionHint ?? undefined}>
            <button
              className="primary-btn"
              onClick={() => onFetchRenders(includeLocalWorkRenders)}
              disabled={isSceneActionBusy || !canRunActions}
            >
              Fetch Renders
            </button>
          </span>
          <span className="scene-action-with-hint" data-hint={fetchActionHint ?? undefined}>
            <button className="primary-btn" onClick={onFetchGltf} disabled={isSceneActionBusy || !canRunActions}>
              Fetch Scene
            </button>
          </span>
          <DownloadDropdown
            onDownloadGltf={onDownloadGltf}
            onDownloadBlend={onDownloadBlend}
            onDownloadBlendFile={onDownloadBlendFile}
            onListBlendFiles={onListBlendFiles}
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

      <RenderGallery
        renders={renders}
        isLoading={loading.renders}
        backendUrl={backendUrl}
        includeLocalWork={includeLocalWorkRenders}
        onIncludeLocalWorkChange={setIncludeLocalWorkRenders}
      />

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
