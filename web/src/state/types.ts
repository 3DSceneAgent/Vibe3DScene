import type { RenderImage, SceneInfo, TodoItem } from '../api/types'

export type MessageRole = 'user' | 'assistant' | 'tool'
export type MessageStatus = 'streaming' | 'final' | 'error'

export type ToolMedia = {
  kind: 'url' | 'data'
  value: string
}

export type Message = {
  id: string
  role: MessageRole
  content: string
  thinking?: string
  createdAt: number
  raw?: string
  streamId?: string | null
  status?: MessageStatus
  toolName?: string
  toolPayload?: unknown
  toolMedia?: ToolMedia[]
  collapsed?: boolean
}

export type Thread = {
  id: string
  title: string
  createdAt: number
  messages: Message[]
  todos: TodoItem[]
  scene?: SceneInfo | null
  renders?: RenderImage[]
  gltfUrl?: string | null
  sceneHasChange?: boolean
}

export type ThemeId = 'dark' | 'light'

export type Settings = {
  backendUrl: string
  theme: ThemeId
  autoRefreshScene: boolean
  sceneTabCollapsed: boolean
}
