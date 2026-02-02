import type { RenderImage, SceneInfo, TodoItem } from '../api/types'

export type MessageRole = 'user' | 'assistant'
export type MessageStatus = 'streaming' | 'final' | 'error'

export type Message = {
  id: string
  role: MessageRole
  content: string
  thinking?: string
  createdAt: number
  raw?: string
  status?: MessageStatus
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
}

export type ThemeId = 'midnight' | 'slate' | 'warm'

export type Settings = {
  backendUrl: string
  theme: ThemeId
}
