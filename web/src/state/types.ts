import type {
  GraphNodeStream,
  ImageAsset,
  RenderImage,
  SceneArtifactManifestInfo,
  SceneInfo,
  StreamProgress,
  TelemetryMetrics,
  TodoItem
} from '../api/types'
import type { EnvironmentPreset } from '../constants/environmentPresets'

export type MessageRole = 'user' | 'assistant' | 'tool'
export type MessageStatus = 'streaming' | 'final' | 'error'

export type ToolMedia = {
  kind: 'url' | 'data'
  value: string
}

export type PendingImageAttachment = {
  file: File
  previewUrl: string
}

export type SceneObjectReference = {
  backendObjectId: string
  displayName: string
  objectType?: string | null
}

export type SceneObjectReferenceInsertion = {
  key: string
  reference: SceneObjectReference
}

export type SceneObjectTransformMode = 'select' | 'translate' | 'rotate' | 'scale'

export type SceneObjectTransformUpdate = {
  backendObjectId: string
  backendObjectName?: string | null
  worldMatrix: number[][]
  commit: boolean
}

export type SceneHierarchyNode = {
  nodeId: string
  name: string
  type: string
  children: SceneHierarchyNode[]
  backendObjectId?: string | null
  backendObjectName?: string | null
  deletable?: boolean
  transformable?: boolean
  referencable?: boolean
}

export type Message = {
  id: string
  turnId?: string
  role: MessageRole
  content: string
  thinking?: string
  thinkingActive?: boolean
  createdAt: number
  raw?: string
  streamId?: string | null
  status?: MessageStatus
  toolCallKey?: string
  toolCallId?: string
  toolName?: string
  toolPayload?: unknown
  toolMedia?: ToolMedia[]
  attachedImages?: ImageAsset[]
  referencedObjects?: SceneObjectReference[]
  collapsed?: boolean
}

export type ThreadStreamSession = {
  streamRequestId: string | null
  lastEventId: number
  updatedAtMs: number
  progress?: StreamProgress | null
}

export type Thread = {
  id: string
  title: string
  titleEditedManually?: boolean
  createdAt: number
  updatedAtMs?: number
  messages: Message[]
  mcpToolEnabled?: Record<string, boolean>
  fastMode?: boolean
  vlmProvider?: string
  vlmModel?: string
  providerThinking?: boolean | null
  vlmLocked?: boolean
  todos: TodoItem[]
  activeTodoId?: string | null
  scene?: SceneInfo | null
  renders?: RenderImage[]
  gltfUrl?: string | null
  sceneManifest?: SceneArtifactManifestInfo | null
  sceneRevision?: number | null
  sceneHierarchy?: SceneHierarchyNode[]
  sceneHasChange?: boolean
  images?: ImageAsset[]
  graphEvents?: GraphNodeStream[]
  occupyingResources?: boolean
  lastRuntimeActiveMs?: number
  streamSession?: ThreadStreamSession | null
  threadMetrics?: TelemetryMetrics | null
  turnMetricsByTurnId?: Record<string, TelemetryMetrics>
}

export type ThemeId = 'dark' | 'light'
export type ViewportThemeId = 'auto' | 'dark' | 'light'
export type UiModeId = 'default' | 'minimal'

export type Settings = {
  backendUrl: string
  theme: ThemeId
  autoRefreshScene: boolean
  autoFetchIntervalSeconds: number
  providerThinkingDefault: boolean
  maxRequestAgentTurns: number
  maxRequestToolBatches: number
  viewportTheme: ViewportThemeId
  viewportEnvironment: EnvironmentPreset
  environmentLightIntensity: number
  environmentBackgroundIntensity: number
  showViewportGrid: boolean
  showHdriBackground: boolean
  uiMode: UiModeId
}
