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
  image_url: string
}

export type BlendFileEntry = {
  relative_path: string
  filename: string
  size_bytes: number
  modified_at: string
  category: string
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

export type GraphNodeStream = {
  request_id: string
  thread_id: string
  node: string
  step_index: number
  update_keys: string[]
  state_patch?: Record<string, unknown>
  message_count?: number
}

export type StreamEvent = {
  messages?: unknown[]
  todos?: TodoItem[]
  error?: string
  status_code?: number
  delta?: string
  message_id?: string | null
  event?: 'done' | 'graph_node'
  graph_node?: GraphNodeStream
  scene_has_change?: boolean
}

export type McpToolsInfo = {
  thread_id: string
  loaded: boolean
  tool_count: number
  tools: string[]
  tool_hints: Record<string, string>
  blender_mode: 'headless' | 'local-client' | string
}

export type VlmProviderOption = {
  provider: string
  display_name: string
  default_model: string
  models: string[]
  configured: boolean
}

export type ThreadVlmSelection = {
  thread_id: string
  provider: string
  model: string
  locked: boolean
}

export type VlmModelsInfo = {
  providers: VlmProviderOption[]
  default_provider: string
  default_model: string
  thread_selection?: ThreadVlmSelection
}
