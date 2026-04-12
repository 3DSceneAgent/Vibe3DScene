import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from 'react'
import type {
  AddPrimitiveInfo,
  AddPrimitiveRequest,
  BlendFileEntry,
  DeleteSceneObjectInfo,
  PrimitiveType,
  RenderImage
} from '../api/types'
import {
  environmentPresetOptions,
  environmentPresets,
  type EnvironmentPreset
} from '../constants/environmentPresets'
import type {
  SceneHierarchyNode,
  SceneObjectTransformMode,
  SceneObjectTransformUpdate
} from '../state/types'
import { resolveMediaUrl } from '../utils/url'
import { GltfViewer } from './GltfViewer'
import { RenderGallery } from './RenderGallery'
import { SceneInfoPanel } from './SceneInfoPanel'

type ViewportTheme = 'auto' | 'dark' | 'light'
type UiTheme = 'dark' | 'light'
type ShadingMode = 'lit' | 'unlit' | 'wireframe'

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
  onDeleteSceneObject: (
    backendObjectId: string,
    backendObjectName?: string | null
  ) => Promise<DeleteSceneObjectInfo | null>
  onAddPrimitive?: (payload: AddPrimitiveRequest) => Promise<AddPrimitiveInfo | null>
  onTransformSceneObject?: (transform: SceneObjectTransformUpdate) => Promise<boolean>
  onTransformDragChange?: (dragging: boolean) => void
  onDownloadGltf: () => void
  onDownloadBlend: () => void
  onDownloadBlendFile: (relativePath: string, filename: string) => void
  onListBlendFiles: () => Promise<BlendFileEntry[]>
  actionError?: string | null
  onClearActionError?: () => void
  onHierarchyChange: (threadId: string, hierarchy: SceneHierarchyNode[]) => void
  onRefInChat?: (node: SceneHierarchyNode) => void
  loading: {
    scene: boolean
    renders: boolean
    gltf: boolean
    download: boolean
  }
  canRunActions: boolean
  canDeleteHierarchy: boolean
  canTransformObjects?: boolean
  canDownloadPersistedGltf?: boolean
  deleteActionHint?: string | null
  transformActionHint?: string | null
  idleActionHint?: string | null
  sceneFetchCooldownHint?: string | null
  onClaimRuntime?: () => Promise<boolean>
  minimalUi?: boolean
}

type DownloadDropdownProps = {
  onDownloadGltf: () => void
  onDownloadBlend: () => void
  onDownloadBlendFile: (relativePath: string, filename: string) => void
  onListBlendFiles: () => Promise<BlendFileEntry[]>
  loading: boolean
}

type DownloadRequest =
  | { kind: 'gltf' }
  | { kind: 'blend' }
  | { kind: 'blend-file'; relativePath: string; filename: string }

type PrimitiveOption = {
  type: PrimitiveType
  label: string
}

const DEFAULT_RENDER_PANEL_HEIGHT = 275
const MIN_RENDER_PANEL_HEIGHT = 140
const MIN_VIEWPORT_HEIGHT = 260
const LAYOUT_RESIZER_HEIGHT = 14
const KEYBOARD_RESIZE_STEP = 24
const PRIMITIVE_OPTIONS: PrimitiveOption[] = [
  { type: 'cube', label: 'Cube' },
  { type: 'sphere', label: 'Sphere' },
  { type: 'cylinder', label: 'Cylinder' },
  { type: 'plane', label: 'Plane' },
  { type: 'cone', label: 'Cone' },
  { type: 'torus', label: 'Torus' },
  { type: 'icosphere', label: 'Ico Sphere' }
]

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

function PrimitiveOptionIcon({ type }: { type: PrimitiveType }) {
  if (type === 'cube') {
    return (
      <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
        <path d="M5 6.5 9 4l4 2.5-4 2.5z" />
        <path d="M5 6.5V11.5L9 14l4-2.5V6.5" />
        <path d="M9 9V14" />
      </svg>
    )
  }
  if (type === 'sphere') {
    return (
      <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="9" cy="9" r="5.5" />
        <path d="M3.5 9h11" />
        <path d="M9 3.5c1.6 1.4 2.4 3.4 2.4 5.5S10.6 13.1 9 14.5C7.4 13.1 6.6 11.1 6.6 9S7.4 4.9 9 3.5z" />
      </svg>
    )
  }
  if (type === 'cylinder') {
    return (
      <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
        <ellipse cx="9" cy="5" rx="4.5" ry="2.2" />
        <path d="M4.5 5v6c0 1.2 2 2.2 4.5 2.2s4.5-1 4.5-2.2V5" />
        <path d="M4.5 11c0 1.2 2 2.2 4.5 2.2s4.5-1 4.5-2.2" />
      </svg>
    )
  }
  if (type === 'plane') {
    return (
      <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
        <path d="M4 11 9 6l5 1.5-5 4.5z" />
      </svg>
    )
  }
  if (type === 'cone') {
    return (
      <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
        <path d="M9 4.2 4.7 12.3h8.6z" />
        <ellipse cx="9" cy="12.3" rx="4.3" ry="1.5" />
      </svg>
    )
  }
  if (type === 'torus') {
    return (
      <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
        <ellipse cx="9" cy="9" rx="5.2" ry="3.8" />
        <ellipse cx="9" cy="9" rx="2.2" ry="1.6" />
      </svg>
    )
  }
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 3.7 13.8 6.5v5L9 14.3l-4.8-2.8v-5z" />
      <path d="M9 3.7v10.6" />
      <path d="M4.2 6.5 9 9.2l4.8-2.7" />
    </svg>
  )
}

function hierarchyContainsNodeId(hierarchy: SceneHierarchyNode[], nodeId: string | null): boolean {
  if (!nodeId) return false
  for (const node of hierarchy) {
    if (node.nodeId === nodeId) {
      return true
    }
    if (hierarchyContainsNodeId(node.children, nodeId)) {
      return true
    }
  }
  return false
}

function hierarchyContainsBackendObjectId(
  hierarchy: SceneHierarchyNode[],
  backendObjectId: string | null
): boolean {
  if (!backendObjectId) return false
  for (const node of hierarchy) {
    if (node.backendObjectId === backendObjectId) {
      return true
    }
    if (hierarchyContainsBackendObjectId(node.children, backendObjectId)) {
      return true
    }
  }
  return false
}

function filterHierarchyByHiddenBackendObjectIds(
  hierarchy: SceneHierarchyNode[],
  hiddenBackendObjectIds: Set<string>
): SceneHierarchyNode[] {
  return hierarchy.flatMap((node) => {
    if (node.backendObjectId && hiddenBackendObjectIds.has(node.backendObjectId)) {
      return []
    }

    const children = filterHierarchyByHiddenBackendObjectIds(node.children, hiddenBackendObjectIds)
    if (!node.backendObjectId && node.children.length > 0 && children.length === 0) {
      return []
    }

    return [{ ...node, children }]
  })
}

function DownloadDropdown({
  onDownloadGltf,
  onDownloadBlend,
  onDownloadBlendFile,
  onListBlendFiles,
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
        disabled={loading}
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
            disabled={loading}
          >
            Download GLTF (.glb)
          </button>
          <button
            className="dropdown-item"
            onClick={() => {
              onDownloadBlend()
              setIsOpen(false)
            }}
            disabled={loading}
          >
            Download Current BLEND (.blend)
          </button>
          <div className="dropdown-section-label">Persisted BLEND Files</div>
          {isLoadingBlendFiles && <div className="dropdown-item dropdown-item-info">Loading...</div>}
          {!isLoadingBlendFiles && blendFilesError && (
            <button className="dropdown-item" onClick={() => void loadBlendFiles()} disabled={loading}>
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
                disabled={loading}
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
  onDeleteSceneObject,
  onAddPrimitive,
  onTransformSceneObject,
  onTransformDragChange,
  onDownloadGltf,
  onDownloadBlend,
  onDownloadBlendFile,
  onListBlendFiles,
  actionError,
  onClearActionError,
  onHierarchyChange,
  onRefInChat,
  loading,
  canRunActions,
  canDeleteHierarchy,
  canTransformObjects = false,
  canDownloadPersistedGltf = false,
  deleteActionHint = null,
  transformActionHint = null,
  idleActionHint = null,
  sceneFetchCooldownHint = null,
  onClaimRuntime,
  minimalUi = false
}: SceneTabProps) {
  const [objectsCollapsed, setObjectsCollapsed] = useState(true)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [alwaysAutoFrameCamera] = useState(false)
  const [twoSidedRendering, setTwoSidedRendering] = useState(false)
  const [isEnvironmentMenuOpen, setIsEnvironmentMenuOpen] = useState(false)
  const [includeLocalWorkRenders, setIncludeLocalWorkRenders] = useState(false)
  const [renderPanelHeight, setRenderPanelHeight] = useState(DEFAULT_RENDER_PANEL_HEIGHT)
  const [layoutHeight, setLayoutHeight] = useState(0)
  const [isResizingLayout, setIsResizingLayout] = useState(false)
  const [selectedBackendObjectId, setSelectedBackendObjectId] = useState<string | null>(null)
  const [pendingAutoSelectedBackendObjectId, setPendingAutoSelectedBackendObjectId] = useState<string | null>(null)
  const [deletingNodeId, setDeletingNodeId] = useState<string | null>(null)
  const [claimingForDeleteNodeId, setClaimingForDeleteNodeId] = useState<string | null>(null)
  const [addingPrimitiveType, setAddingPrimitiveType] = useState<PrimitiveType | null>(null)
  const [isPrimitivePopoverOpen, setIsPrimitivePopoverOpen] = useState(false)
  const [confirmDialog, setConfirmDialog] = useState<{
    mode: 'claim-delete' | 'delete' | 'claim-transform' | 'claim-download' | 'claim-fetch' | 'claim-add-primitive'
    node?: SceneHierarchyNode
    transformMode?: SceneObjectTransformMode
    downloadRequest?: DownloadRequest
    primitiveType?: PrimitiveType
  } | null>(null)
  const [claimingTransformMode, setClaimingTransformMode] = useState<SceneObjectTransformMode | null>(null)
  const [shadingMode, setShadingMode] = useState<ShadingMode>('lit')
  const [transformMode, setTransformMode] = useState<SceneObjectTransformMode>('select')
  const [menuFixedPos, setMenuFixedPos] = useState<{ top: number; left: number } | null>(null)
  const [undoCount, setUndoCount] = useState(0)
  const [focusViewportRequest, setFocusViewportRequest] = useState(0)
  const [optimisticallyHiddenBackendObjectIds, setOptimisticallyHiddenBackendObjectIds] = useState<string[]>([])
  const [addToChatConfirmDialog, setAddToChatConfirmDialog] = useState<{
    backendObjectId: string
    backendObjectName: string | null
  } | null>(null)
  const [addToChatSkipConfirm, setAddToChatSkipConfirm] = useState(() => {
    try { return localStorage.getItem('scene-agent-add-to-chat-skip-confirm') === '1' } catch { return false }
  })
  const undoFnRef = useRef<(() => boolean) | null>(null)
  const layoutRef = useRef<HTMLDivElement | null>(null)
  const environmentMenuRef = useRef<HTMLDivElement | null>(null)
  const primitivePopoverRef = useRef<HTMLDivElement | null>(null)
  const settingsTriggerRef = useRef<HTMLButtonElement | null>(null)
  const showRenderGallery = !minimalUi
  const hasRenders = showRenderGallery && renders.length > 0
  const isSceneActionBusy = loading.scene || loading.renders || loading.gltf
  const fetchActionHint = !canRunActions
    ? idleActionHint
    : isSceneActionBusy
      ? sceneFetchCooldownHint
      : null
  const isHierarchyCollapsed = minimalUi || objectsCollapsed
  const resolvedGltfUrl = resolveMediaUrl(gltfUrl ?? undefined, backendUrl) ?? null
  const viewerKey = threadId
  const hiddenBackendObjectIdSet = useMemo(
    () => new Set(optimisticallyHiddenBackendObjectIds),
    [optimisticallyHiddenBackendObjectIds]
  )
  const visibleSceneHierarchy = useMemo(
    () => filterHierarchyByHiddenBackendObjectIds(sceneHierarchy, hiddenBackendObjectIdSet),
    [hiddenBackendObjectIdSet, sceneHierarchy]
  )
  const handleHierarchyChange = useCallback(
    (hierarchy: SceneHierarchyNode[]) => onHierarchyChange(threadId, hierarchy),
    [onHierarchyChange, threadId]
  )

  useEffect(() => {
    setSelectedBackendObjectId(null)
    setPendingAutoSelectedBackendObjectId(null)
    setDeletingNodeId(null)
    setAddingPrimitiveType(null)
    setIsPrimitivePopoverOpen(false)
    setConfirmDialog(null)
    setClaimingTransformMode(null)
    setShadingMode('lit')
    setTransformMode('select')
    setUndoCount(0)
    setOptimisticallyHiddenBackendObjectIds([])
    setAddToChatConfirmDialog(null)
  }, [threadId])

  const executeDownloadRequest = useCallback(
    (request: DownloadRequest) => {
      if (request.kind === 'gltf') {
        onDownloadGltf()
        return
      }
      if (request.kind === 'blend') {
        onDownloadBlend()
        return
      }
      onDownloadBlendFile(request.relativePath, request.filename)
    },
    [onDownloadBlend, onDownloadBlendFile, onDownloadGltf]
  )

  const queueAddedPrimitiveSelection = useCallback((backendObjectId: string | null | undefined) => {
    const normalized = typeof backendObjectId === 'string' ? backendObjectId.trim() : ''
    if (!normalized) {
      return
    }
    setPendingAutoSelectedBackendObjectId(normalized)
  }, [])

  const handleDialogConfirm = useCallback(async () => {
    if (!confirmDialog) return
    const { mode, node, transformMode: pendingTransformMode, downloadRequest, primitiveType } = confirmDialog

    if (mode === 'claim-delete') {
      if (!onClaimRuntime || !node?.backendObjectId) {
        setConfirmDialog(null)
        return
      }
      setConfirmDialog(null)
      setClaimingForDeleteNodeId(node.nodeId)
      try {
        const claimed = await onClaimRuntime()
        if (!claimed) return
      } finally {
        setClaimingForDeleteNodeId(null)
      }
      setConfirmDialog({ mode: 'delete', node })
      return
    }

    if (mode === 'claim-transform') {
      if (!onClaimRuntime || !pendingTransformMode) {
        setConfirmDialog(null)
        return
      }
      setConfirmDialog(null)
      setClaimingTransformMode(pendingTransformMode)
      try {
        const claimed = await onClaimRuntime()
        if (!claimed) return
        setTransformMode(pendingTransformMode)
      } finally {
        setClaimingTransformMode(null)
      }
      return
    }

    if (mode === 'claim-download') {
      if (!onClaimRuntime || !downloadRequest) {
        setConfirmDialog(null)
        return
      }
      setConfirmDialog(null)
      const claimed = await onClaimRuntime()
      if (!claimed) return
      executeDownloadRequest(downloadRequest)
      return
    }

    if (mode === 'claim-add-primitive') {
      if (!onClaimRuntime || !onAddPrimitive || !primitiveType) {
        setConfirmDialog(null)
        return
      }
      setConfirmDialog(null)
      const claimed = await onClaimRuntime()
      if (!claimed) return
      setAddingPrimitiveType(primitiveType)
      try {
        const result = await onAddPrimitive({ primitive_type: primitiveType })
        if (result?.backend_object_id) {
          queueAddedPrimitiveSelection(result.backend_object_id)
        }
      } finally {
        setAddingPrimitiveType((current) => (current === primitiveType ? null : current))
      }
      return
    }

    if (mode === 'claim-fetch') {
      if (!onClaimRuntime) {
        setConfirmDialog(null)
        return
      }
      setConfirmDialog(null)
      const claimed = await onClaimRuntime()
      if (!claimed) return
      onFetchGltf()
      return
    }

    if (mode === 'delete' && node?.backendObjectId) {
      setConfirmDialog(null)
      setDeletingNodeId(node.nodeId)
      const shouldRestoreSelection = selectedBackendObjectId === node.backendObjectId
      if (shouldRestoreSelection) {
        setSelectedBackendObjectId(null)
      }
      setOptimisticallyHiddenBackendObjectIds((current) =>
        current.includes(node.backendObjectId!) ? current : [...current, node.backendObjectId!]
      )
      try {
        const result = await onDeleteSceneObject(node.backendObjectId, node.backendObjectName ?? node.name)
        if (!result && shouldRestoreSelection) {
          setSelectedBackendObjectId(node.backendObjectId)
        }
        if (!result) {
          setOptimisticallyHiddenBackendObjectIds((current) =>
            current.filter((entry) => entry !== node.backendObjectId)
          )
        }
      } finally {
        setDeletingNodeId((current) => (current === node.nodeId ? null : current))
      }
    }
  }, [
    confirmDialog,
    executeDownloadRequest,
    onAddPrimitive,
    onClaimRuntime,
    onDeleteSceneObject,
    onFetchGltf,
    queueAddedPrimitiveSelection,
    selectedBackendObjectId
  ])

  const canRequestTransformTools = canTransformObjects || Boolean(onClaimRuntime)
  const canRequestPrimitiveTools = Boolean(onAddPrimitive) && (canTransformObjects || Boolean(onClaimRuntime))

  const shouldClaimBeforeDownload = useCallback(
    (request: DownloadRequest) => {
      if (canRunActions || !onClaimRuntime) {
        return false
      }
      if (request.kind === 'blend-file') {
        return false
      }
      if (request.kind === 'gltf' && canDownloadPersistedGltf) {
        return false
      }
      return true
    },
    [canDownloadPersistedGltf, canRunActions, onClaimRuntime]
  )

  const requestDownload = useCallback(
    (request: DownloadRequest) => {
      if (loading.download) {
        return
      }
      if (shouldClaimBeforeDownload(request)) {
        setConfirmDialog({ mode: 'claim-download', downloadRequest: request })
        return
      }
      executeDownloadRequest(request)
    },
    [executeDownloadRequest, loading.download, shouldClaimBeforeDownload]
  )

  const requestFetchScene = useCallback(() => {
    if (isSceneActionBusy) {
      return
    }
    if (canRunActions) {
      onFetchGltf()
      return
    }
    if (onClaimRuntime) {
      setConfirmDialog({ mode: 'claim-fetch' })
    }
  }, [canRunActions, isSceneActionBusy, onClaimRuntime, onFetchGltf])

  const requestTransformMode = useCallback(
    (nextMode: SceneObjectTransformMode) => {
      if (nextMode === 'select') {
        setTransformMode('select')
        return
      }
      if (canTransformObjects) {
        setTransformMode(nextMode)
        return
      }
      if (!onClaimRuntime) {
        return
      }
      setConfirmDialog({ mode: 'claim-transform', transformMode: nextMode })
    },
    [canTransformObjects, onClaimRuntime]
  )

  const requestAddPrimitive = useCallback(
    async (primitiveType: PrimitiveType) => {
      if (!onAddPrimitive || addingPrimitiveType !== null) {
        return
      }
      setIsPrimitivePopoverOpen(false)

      if (!canTransformObjects) {
        if (onClaimRuntime) {
          setConfirmDialog({ mode: 'claim-add-primitive', primitiveType })
        }
        return
      }

      setAddingPrimitiveType(primitiveType)
      try {
        const result = await onAddPrimitive({ primitive_type: primitiveType })
        if (result?.backend_object_id) {
          queueAddedPrimitiveSelection(result.backend_object_id)
        }
      } finally {
        setAddingPrimitiveType((current) => (current === primitiveType ? null : current))
      }
    },
    [addingPrimitiveType, canTransformObjects, onAddPrimitive, onClaimRuntime, queueAddedPrimitiveSelection]
  )

  const findNodeByBackendObjectId = useCallback(
    (hierarchy: SceneHierarchyNode[], objectId: string): SceneHierarchyNode | null => {
      for (const node of hierarchy) {
        if (node.backendObjectId === objectId) return node
        const found = findNodeByBackendObjectId(node.children, objectId)
        if (found) return found
      }
      return null
    },
    []
  )

  const handleAddToChat = useCallback(
    (backendObjectId: string, backendObjectName: string | null) => {
      if (!onRefInChat) return
      if (addToChatSkipConfirm) {
        const node = findNodeByBackendObjectId(visibleSceneHierarchy, backendObjectId)
        if (node) onRefInChat(node)
        return
      }
      setAddToChatConfirmDialog({ backendObjectId, backendObjectName })
    },
    [onRefInChat, addToChatSkipConfirm, visibleSceneHierarchy, findNodeByBackendObjectId]
  )

  const handleRequestDeleteSelected = useCallback((backendObjectId: string) => {
    const node = findNodeByBackendObjectId(sceneHierarchy, backendObjectId)
    if (!node || deletingNodeId) {
      return
    }
    if (!canDeleteHierarchy && onClaimRuntime) {
      setConfirmDialog({ mode: 'claim-delete', node })
      return
    }
    if (!canDeleteHierarchy) {
      return
    }
    setConfirmDialog({ mode: 'delete', node })
  }, [canDeleteHierarchy, deletingNodeId, findNodeByBackendObjectId, onClaimRuntime, sceneHierarchy])

  const handleSelectNodeFromHierarchy = useCallback((backendObjectId: string) => {
    setPendingAutoSelectedBackendObjectId(null)
    setSelectedBackendObjectId(backendObjectId)
    setFocusViewportRequest((current) => current + 1)
  }, [])

  const handleSelectBackendObjectFromViewport = useCallback((backendObjectId: string | null) => {
    setPendingAutoSelectedBackendObjectId(null)
    setSelectedBackendObjectId(backendObjectId)
  }, [])

  const confirmAddToChat = useCallback(
    (dontAskAgain: boolean) => {
      if (!addToChatConfirmDialog || !onRefInChat) {
        setAddToChatConfirmDialog(null)
        return
      }
      if (dontAskAgain) {
        setAddToChatSkipConfirm(true)
        try { localStorage.setItem('scene-agent-add-to-chat-skip-confirm', '1') } catch { /* */ }
      }
      const node = findNodeByBackendObjectId(visibleSceneHierarchy, addToChatConfirmDialog.backendObjectId)
      setAddToChatConfirmDialog(null)
      if (node) onRefInChat(node)
    },
    [addToChatConfirmDialog, onRefInChat, visibleSceneHierarchy, findNodeByBackendObjectId]
  )

  useEffect(() => {
    if (
      pendingAutoSelectedBackendObjectId &&
      hierarchyContainsBackendObjectId(visibleSceneHierarchy, pendingAutoSelectedBackendObjectId)
    ) {
      setSelectedBackendObjectId(pendingAutoSelectedBackendObjectId)
      setPendingAutoSelectedBackendObjectId(null)
      setFocusViewportRequest((current) => current + 1)
      return
    }
    if (selectedBackendObjectId && !hierarchyContainsBackendObjectId(visibleSceneHierarchy, selectedBackendObjectId)) {
      setSelectedBackendObjectId(null)
    }
    if (deletingNodeId && !hierarchyContainsNodeId(visibleSceneHierarchy, deletingNodeId)) {
      setDeletingNodeId(null)
    }
    if (claimingForDeleteNodeId && !hierarchyContainsNodeId(visibleSceneHierarchy, claimingForDeleteNodeId)) {
      setClaimingForDeleteNodeId(null)
    }
  }, [
    claimingForDeleteNodeId,
    deletingNodeId,
    pendingAutoSelectedBackendObjectId,
    visibleSceneHierarchy,
    selectedBackendObjectId
  ])

  useEffect(() => {
    setOptimisticallyHiddenBackendObjectIds((current) =>
      current.filter((backendObjectId) => hierarchyContainsBackendObjectId(sceneHierarchy, backendObjectId))
    )
  }, [sceneHierarchy])

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
    if (!isPrimitivePopoverOpen) return

    const handlePointerDown = (event: MouseEvent) => {
      if (
        primitivePopoverRef.current &&
        !primitivePopoverRef.current.contains(event.target as Node)
      ) {
        setIsPrimitivePopoverOpen(false)
      }
    }

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsPrimitivePopoverOpen(false)
      }
    }

    document.addEventListener('mousedown', handlePointerDown)
    window.addEventListener('keydown', handleEscape)
    return () => {
      document.removeEventListener('mousedown', handlePointerDown)
      window.removeEventListener('keydown', handleEscape)
    }
  }, [isPrimitivePopoverOpen])

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
          ref={settingsTriggerRef}
          type="button"
          className={`ghost-btn icon-btn viewer-toolbar-btn viewer-settings-trigger ${isEnvironmentMenuOpen ? 'is-active' : ''}`}
          aria-haspopup="dialog"
          aria-expanded={isEnvironmentMenuOpen}
          onClick={() => {
            setIsEnvironmentMenuOpen((open) => {
              if (!open && settingsTriggerRef.current) {
                const rect = settingsTriggerRef.current.getBoundingClientRect()
                const menuW = 248
                const pad = 8
                const left = Math.max(pad, Math.min(rect.right - menuW, window.innerWidth - menuW - pad))
                setMenuFixedPos({ top: rect.bottom + pad, left })
              }
              return !open
            })
          }}
          title="Viewport settings"
          aria-label="Viewport settings"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12.22 2h-.44a2 2 0 0 0-2 1.82l-.2 2.1a7.5 7.5 0 0 0-1.67.95L6 5.74a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l1.8 1.04a7.5 7.5 0 0 0 0 1.92l-1.8 1.04a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l1.91-1.13a7.5 7.5 0 0 0 1.67.96l.2 2.09A2 2 0 0 0 11.78 22h.44a2 2 0 0 0 2-1.82l.2-2.1a7.5 7.5 0 0 0 1.67-.95L18 18.26a2 2 0 0 0 2.73-.73l.22-.38a2 2 0 0 0-.73-2.73l-1.8-1.04a7.5 7.5 0 0 0 0-1.92l1.8-1.04a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-1.91 1.13a7.5 7.5 0 0 0-1.67-.96l-.2-2.09A2 2 0 0 0 12.22 2z" />
            <circle cx="12" cy="12" r="3" />
          </svg>
        </button>
        {isEnvironmentMenuOpen && menuFixedPos && (
          <div
            className="viewer-environment-menu"
            role="dialog"
            aria-label="Viewport display controls"
            style={{ top: menuFixedPos.top, left: menuFixedPos.left }}
          >
            <div className="viewer-environment-menu-section">
              <label className="viewer-environment-menu-field" htmlFor={`viewer-environment-${threadId}`}>
                <span className="viewer-environment-label">Lighting</span>
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
            <div className="viewer-environment-menu-section">
              <label className="viewer-environment-menu-field" htmlFor={`viewer-shading-${threadId}`}>
                <span className="viewer-environment-label">Shading</span>
                <select
                  id={`viewer-shading-${threadId}`}
                  className="styled-select viewer-environment-select"
                  value={shadingMode}
                  aria-label="Viewport shading"
                  onChange={(event) => setShadingMode(event.target.value as ShadingMode)}
                >
                  <option value="lit">Lit</option>
                  <option value="unlit">Unlit</option>
                  <option value="wireframe">Wireframe</option>
                </select>
              </label>
            </div>
            <div className="viewer-environment-menu-section">
              <label className="viewer-environment-menu-toggle" htmlFor={`viewer-two-sided-${threadId}`}>
                <span className="viewer-environment-label">Two-Sided</span>
                <input
                  id={`viewer-two-sided-${threadId}`}
                  type="checkbox"
                  className="viewer-environment-checkbox"
                  checked={twoSidedRendering}
                  onChange={() => setTwoSidedRendering((v) => !v)}
                  aria-label="Toggle two-sided rendering"
                />
              </label>
            </div>
          </div>
        )}
      </div>
      <div className="viewer-control-group">
        <span className="viewer-toolbar-btn-shell" data-hint={fetchActionHint ?? 'Fetch scene'}>
          <button
            type="button"
            className="ghost-btn icon-btn viewer-toolbar-btn"
            onClick={requestFetchScene}
            disabled={isSceneActionBusy || (!canRunActions && !onClaimRuntime)}
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
        </span>
        <DownloadDropdown
          onDownloadGltf={() => requestDownload({ kind: 'gltf' })}
          onDownloadBlend={() => requestDownload({ kind: 'blend' })}
          onDownloadBlendFile={(relativePath, filename) =>
            requestDownload({ kind: 'blend-file', relativePath, filename })
          }
          onListBlendFiles={onListBlendFiles}
          loading={loading.download}
        />
      </div>
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
          shadingMode={shadingMode}
          selectedBackendObjectId={selectedBackendObjectId}
          hiddenBackendObjectIds={optimisticallyHiddenBackendObjectIds}
          transformEnabled={canTransformObjects}
          canRequestTransform={canRequestTransformTools}
          transformMode={transformMode}
          alwaysAutoFrameCamera={alwaysAutoFrameCamera}
          focusViewportRequest={focusViewportRequest}
          onHierarchyChange={handleHierarchyChange}
          onSelectBackendObject={handleSelectBackendObjectFromViewport}
          onTransformModeChange={requestTransformMode}
          onTransformObject={onTransformSceneObject}
          onTransformDragChange={onTransformDragChange}
          onAddToChat={handleAddToChat}
          onRequestDeleteSelected={handleRequestDeleteSelected}
          undoRef={undoFnRef}
          onUndoCountChange={setUndoCount}
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
          footerControls={
            <div className="viewer-bottom-bar-group" role="toolbar" aria-label="Viewport transform tools">
              <span className="viewer-toolbar-btn-shell" data-hint="Select (Q)">
                <button
                  type="button"
                  className={`ghost-btn icon-btn viewer-toolbar-btn viewer-transform-btn ${transformMode === 'select' ? 'is-active' : ''}`}
                  onClick={() => requestTransformMode('select')}
                  aria-label="Select object"
                >
                  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M3 2l4.5 12 1.8-4.7L14 7.5z" />
                  </svg>
                </button>
              </span>
              <span className="viewer-bottom-bar-sep" aria-hidden="true" />
              <span
                className="viewer-toolbar-btn-shell"
                data-hint={!selectedBackendObjectId ? 'Select an object first' : (transformActionHint ?? 'Move (W)')}
              >
                <button
                  type="button"
                  className={`ghost-btn icon-btn viewer-toolbar-btn viewer-transform-btn ${transformMode === 'translate' ? 'is-active' : ''}`}
                  onClick={() => requestTransformMode('translate')}
                  disabled={!canRequestTransformTools || claimingTransformMode !== null || !selectedBackendObjectId}
                  aria-label="Move object"
                >
                  {claimingTransformMode === 'translate' ? (
                    <svg className="spin" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                      <path d="M14 8a6 6 0 1 1-1.5-4" />
                    </svg>
                  ) : (
                    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M8 1.5v13M1.5 8h13" />
                      <path d="M8 1.5 6.3 3.2M8 1.5l1.7 1.7M8 14.5l-1.7-1.7M8 14.5l1.7-1.7M1.5 8l1.7-1.7M1.5 8l1.7 1.7M14.5 8l-1.7-1.7M14.5 8l-1.7 1.7" />
                    </svg>
                  )}
                </button>
              </span>
              <span
                className="viewer-toolbar-btn-shell"
                data-hint={!selectedBackendObjectId ? 'Select an object first' : (transformActionHint ?? 'Rotate (E)')}
              >
                <button
                  type="button"
                  className={`ghost-btn icon-btn viewer-toolbar-btn viewer-transform-btn ${transformMode === 'rotate' ? 'is-active' : ''}`}
                  onClick={() => requestTransformMode('rotate')}
                  disabled={!canRequestTransformTools || claimingTransformMode !== null || !selectedBackendObjectId}
                  aria-label="Rotate object"
                >
                  {claimingTransformMode === 'rotate' ? (
                    <svg className="spin" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                      <path d="M14 8a6 6 0 1 1-1.5-4" />
                    </svg>
                  ) : (
                    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M12.5 6A4.5 4.5 0 1 0 8 12.5" />
                      <path d="M9.5 3.2h3v3" />
                      <path d="M12.5 3.2 8.8 6.9" />
                    </svg>
                  )}
                </button>
              </span>
              <span
                className="viewer-toolbar-btn-shell"
                data-hint={!selectedBackendObjectId ? 'Select an object first' : (transformActionHint ?? 'Scale (R)')}
              >
                <button
                  type="button"
                  className={`ghost-btn icon-btn viewer-toolbar-btn viewer-transform-btn ${transformMode === 'scale' ? 'is-active' : ''}`}
                  onClick={() => requestTransformMode('scale')}
                  disabled={!canRequestTransformTools || claimingTransformMode !== null || !selectedBackendObjectId}
                  aria-label="Scale object"
                >
                  {claimingTransformMode === 'scale' ? (
                    <svg className="spin" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                      <path d="M14 8a6 6 0 1 1-1.5-4" />
                    </svg>
                  ) : (
                    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                      <rect x="2" y="2" width="4" height="4" rx="0.8" />
                      <rect x="10" y="10" width="4" height="4" rx="0.8" />
                      <path d="M6 6l4 4M8.8 10H10v1.2M6 7.2V6h1.2" />
                    </svg>
                  )}
                </button>
              </span>
              <span className="viewer-bottom-bar-sep" aria-hidden="true" />
              <span
                className="viewer-toolbar-btn-shell"
                data-hint={selectedBackendObjectId ? 'Add to Chat (A)' : 'Select an object first'}
              >
                <button
                  type="button"
                  className="ghost-btn icon-btn viewer-toolbar-btn viewer-transform-btn"
                  onClick={() => {
                    if (!selectedBackendObjectId) return
                    const node = findNodeByBackendObjectId(visibleSceneHierarchy, selectedBackendObjectId)
                    handleAddToChat(selectedBackendObjectId, node?.backendObjectName ?? node?.name ?? null)
                  }}
                  disabled={!selectedBackendObjectId || !onRefInChat}
                  aria-label="Add to chat"
                >
                  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M2 3h12a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H5l-3 2.5V4a1 1 0 0 1 1-1z" />
                    <path d="M8 6v4M6 8h4" />
                  </svg>
                </button>
              </span>
              <span
                className="viewer-toolbar-btn-shell"
                data-hint={undoCount > 0 ? `Undo (${navigator.platform?.includes('Mac') ? '⌘' : 'Ctrl'}+Z)` : 'Nothing to undo'}
              >
                <button
                  type="button"
                  className="ghost-btn icon-btn viewer-toolbar-btn viewer-transform-btn"
                  onClick={() => undoFnRef.current?.()}
                  disabled={undoCount === 0}
                  aria-label="Undo transform"
                >
                  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M3 7h7a3 3 0 0 1 0 6H8" />
                    <path d="M6 4 3 7l3 3" />
                  </svg>
                </button>
              </span>
              <span className="viewer-bottom-bar-sep" aria-hidden="true" />
              <div
                ref={primitivePopoverRef}
                className={`viewer-toolbar-btn-shell primitive-popover-shell ${isPrimitivePopoverOpen ? 'is-open' : ''}`}
                data-hint={
                  addingPrimitiveType
                    ? `Adding ${PRIMITIVE_OPTIONS.find((option) => option.type === addingPrimitiveType)?.label ?? 'Primitive'}...`
                    : canRequestPrimitiveTools
                      ? 'Add primitive'
                      : 'Add primitive unavailable'
                }
              >
                <button
                  type="button"
                  className={`ghost-btn icon-btn viewer-toolbar-btn viewer-transform-btn ${isPrimitivePopoverOpen ? 'is-active' : ''}`}
                  onClick={() => {
                    if (!canRequestPrimitiveTools || addingPrimitiveType !== null) {
                      return
                    }
                    setIsPrimitivePopoverOpen((open) => !open)
                  }}
                  disabled={!canRequestPrimitiveTools || addingPrimitiveType !== null}
                  aria-label="Add primitive"
                  aria-haspopup="menu"
                  aria-expanded={isPrimitivePopoverOpen}
                >
                  {addingPrimitiveType ? (
                    <svg className="spin" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                      <path d="M14 8a6 6 0 1 1-1.5-4" />
                    </svg>
                  ) : (
                    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M8 3v10M3 8h10" />
                    </svg>
                  )}
                </button>
                {isPrimitivePopoverOpen && (
                  <div className="primitive-popover" role="menu" aria-label="Add primitive">
                    {PRIMITIVE_OPTIONS.map((option) => (
                      <button
                        key={option.type}
                        type="button"
                        className="primitive-option"
                        onClick={() => {
                          void requestAddPrimitive(option.type)
                        }}
                        disabled={addingPrimitiveType !== null}
                        role="menuitem"
                      >
                        <span className="primitive-option-icon" aria-hidden="true">
                          <PrimitiveOptionIcon type={option.type} />
                        </span>
                        <span className="primitive-option-label">{option.label}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
          }
        />
      </div>
      {!minimalUi && !objectsCollapsed && (
        <SceneInfoPanel
          hierarchy={visibleSceneHierarchy}
          collapsed={false}
          onToggleCollapse={() => setObjectsCollapsed((value) => !value)}
          isSceneSyncing={loading.scene}
          selectedBackendObjectId={selectedBackendObjectId}
          deletingNodeId={deletingNodeId}
          canDeleteHierarchy={canDeleteHierarchy}
          deleteActionHint={deleteActionHint}
          onSelectNode={handleSelectNodeFromHierarchy}
          claimingForDeleteNodeId={claimingForDeleteNodeId}
          onRefInChat={onRefInChat}
          onDeleteNode={(node) => {
            if (!node.backendObjectId || deletingNodeId) {
              return
            }
            if (!canDeleteHierarchy && onClaimRuntime) {
              setConfirmDialog({ mode: 'claim-delete', node })
            } else if (!canDeleteHierarchy) {
              return
            } else {
              setConfirmDialog({ mode: 'delete', node })
            }
          }}
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
            fetchHint={fetchActionHint}
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

      {confirmDialog && (
        <div className="scene-confirm-overlay" onClick={() => setConfirmDialog(null)}>
          <div
            className={`scene-confirm-dialog ${confirmDialog.mode === 'delete' ? 'danger' : ''}`}
            onClick={(e) => e.stopPropagation()}
          >
            <div className={`scene-confirm-title ${confirmDialog.mode === 'delete' ? 'danger' : ''}`}>
              {confirmDialog.mode === 'delete'
                ? 'Delete Object'
                : confirmDialog.mode === 'claim-add-primitive'
                  ? 'Claim Runtime to Add Primitive'
                : confirmDialog.mode === 'claim-download'
                  ? 'Claim Runtime to Download'
                  : confirmDialog.mode === 'claim-fetch'
                    ? 'Claim Runtime to Fetch'
                  : 'Claim Runtime'}
            </div>
            <div className="scene-confirm-body">
              {confirmDialog.mode === 'claim-delete'
                ? 'Runtime is not active for this session. Claim now to enable deleting scene objects?'
                : confirmDialog.mode === 'claim-transform'
                  ? 'Runtime is not active for this session. Claim now to enable viewport transform tools?'
                  : confirmDialog.mode === 'claim-add-primitive'
                    ? 'Runtime is not active for this session. Claim now to add a primitive to the scene?'
                  : confirmDialog.mode === 'claim-download'
                    ? 'Runtime is not active for this session. Claim now before starting this download?'
                    : confirmDialog.mode === 'claim-fetch'
                      ? 'Runtime is not active for this session. Claim now before fetching the latest scene GLTF?'
                  : `Delete "${confirmDialog.node?.backendObjectName ?? confirmDialog.node?.name}" and its descendants from the Blender scene?`}
              {confirmDialog.mode === 'delete' ? (
                <div className="scene-confirm-warning">This action cannot be undone.</div>
              ) : null}
            </div>
            <div className="scene-confirm-actions">
              <button
                type="button"
                className="ghost-btn scene-confirm-cancel"
                onClick={() => setConfirmDialog(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className={`primary-btn scene-confirm-ok ${confirmDialog.mode === 'delete' ? 'danger' : ''}`}
                onClick={() => void handleDialogConfirm()}
              >
                {confirmDialog.mode === 'claim-transform'
                  ? 'Claim & Enable'
                  : confirmDialog.mode === 'claim-add-primitive'
                    ? 'Claim & Add'
                  : confirmDialog.mode === 'claim-download'
                    ? 'Claim & Download'
                    : confirmDialog.mode === 'claim-fetch'
                      ? 'Claim & Fetch'
                  : confirmDialog.mode === 'claim-delete'
                    ? 'Claim & Continue'
                    : 'Delete'}
              </button>
            </div>
          </div>
        </div>
      )}

      {addToChatConfirmDialog && (
        <div className="scene-confirm-overlay" onClick={() => setAddToChatConfirmDialog(null)}>
          <div className="scene-confirm-dialog" onClick={(e) => e.stopPropagation()}>
            <div className="scene-confirm-title">Add to Chat</div>
            <div className="scene-confirm-body">
              {`Reference "${addToChatConfirmDialog.backendObjectName ?? addToChatConfirmDialog.backendObjectId}" in the current conversation?`}
            </div>
            <div className="scene-confirm-actions">
              <label className="scene-confirm-dont-ask" title="Skip this confirmation next time">
                <input
                  type="checkbox"
                  onChange={(e) => {
                    if (e.target.checked) {
                      confirmAddToChat(true)
                    }
                  }}
                />
                <span>Don't ask again</span>
              </label>
              <button
                type="button"
                className="ghost-btn scene-confirm-cancel"
                onClick={() => setAddToChatConfirmDialog(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="primary-btn scene-confirm-ok"
                onClick={() => confirmAddToChat(false)}
              >
                Add
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
