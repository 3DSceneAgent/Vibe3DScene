export type TodoItem = {
  id: string
  description: string
  status: 'pending' | 'in_progress' | 'completed' | 'failed'
  created_at?: string
  completed_at?: string | null
}

export type SceneObject = {
  type?: string
  location?: number[]
  dimensions?: number[]
  bounding_box?: number[][]
  visible?: boolean
  material_count?: number
}

export type SceneInfo = {
  thread_id: string
  scene_objects: Record<string, SceneObject>
  persistent_cameras?: string[]
  iteration_count?: number
}

export type RenderImage = {
  camera_name: string
  image_base64: string
}

export type ReferenceImage = {
  id: string
  thread_id: string
  filename: string
  content_type: string
  size_bytes: number
  sha256: string
  uploaded_at: string
  previewUrl?: string
}

export type StreamEvent = {
  messages?: unknown[]
  todos?: TodoItem[]
  error?: string
  delta?: string
  message_id?: string | null
  event?: 'done'
  scene_has_change?: boolean
}

export type McpToolsInfo = {
  thread_id: string
  loaded: boolean
  tool_count: number
  tools: string[]
  blender_mode: 'headless' | 'local-client' | string
}
