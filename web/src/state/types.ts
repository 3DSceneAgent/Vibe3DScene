import type { GraphNodeStream, ImageAsset, RenderImage, SceneInfo, TodoItem } from '../api/types'

export type MessageRole = 'user' | 'assistant' | 'tool'
export type MessageStatus = 'streaming' | 'final' | 'error'

export type ToolMedia = {
  kind: 'url' | 'data'
  value: string
}

export type SceneHierarchyNode = {
  id: string
  name: string
  type: string
  children: SceneHierarchyNode[]
}

export type Message = {
  id: string
  role: MessageRole
  content: string
  thinking?: string
  thinkingActive?: boolean
  createdAt: number
  raw?: string
  streamId?: string | null
  status?: MessageStatus
  toolCallKey?: string
  toolName?: string
  toolPayload?: unknown
  toolMedia?: ToolMedia[]
  collapsed?: boolean
}

export type Thread = {
  id: string
  title: string
  titleEditedManually?: boolean
  createdAt: number
  messages: Message[]
  mcpToolEnabled?: Record<string, boolean>
  fastMode?: boolean
  vlmProvider?: string
  vlmModel?: string
  vlmLocked?: boolean
  todos: TodoItem[]
  scene?: SceneInfo | null
  renders?: RenderImage[]
  gltfUrl?: string | null
  sceneHierarchy?: SceneHierarchyNode[]
  sceneHasChange?: boolean
  images?: ImageAsset[]
  graphEvents?: GraphNodeStream[]
  occupyingResources?: boolean
  lastRuntimeActiveMs?: number
}

export type ThemeId = 'dark' | 'light'
export type ViewportThemeId = 'auto' | 'dark' | 'light'
export type UiModeId = 'default' | 'minimal'

export type Settings = {
  backendUrl: string
  theme: ThemeId
  autoRefreshScene: boolean
  autoFetchIntervalSeconds: number
  viewportTheme: ViewportThemeId
  uiMode: UiModeId
}
