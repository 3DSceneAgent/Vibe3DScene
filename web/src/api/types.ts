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

export type ImageAsset = {
  id: string
  thread_id: string
  filename: string
  content_type: string
  size_bytes: number
  sha256: string
  uploaded_at: string
  source?: string
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

export type StreamProgress = {
  request_id?: string
  stream_request_id?: string
  task_mode?: string
  graph_steps?: number
  last_node?: string | null
  tool_events?: number
  assistant_chunks?: number
  todo_total?: number
  todo_completed?: number
  latest_seq?: number
  scene_has_change?: boolean
  done?: boolean
}

export type StreamEvent = {
  messages?: unknown[]
  todos?: TodoItem[]
  error?: string
  reason?: string
  status_code?: number
  delta?: string
  message_id?: string | null
  event?: 'done' | 'graph_node' | 'heartbeat'
  graph_node?: GraphNodeStream
  scene_has_change?: boolean
  seq?: number
  stream_request_id?: string
  progress?: StreamProgress
}

export type McpToolsInfo = {
  thread_id: string
  loaded: boolean
  tool_count: number
  tools: string[]
  tool_hints: Record<string, string>
  blender_mode: 'headless' | 'local-client' | string
}

export type HeadlessRuntimeThreadEntry = {
  thread_id: string
  frontend_client_id: string
  status: string
  last_active_ms: number
  occupying_resources: boolean
  blender_port?: number | null
  mcp_port?: number | null
}

export type HeadlessSessionCapacityInfo = {
  blender_mode: string
  frontend_client_id: string
  quota: number
  in_use: number
  occupying_threads: HeadlessRuntimeThreadEntry[]
}

export type ReleaseRuntimeInfo = {
  thread_id: string
  released: boolean
  cleaned: string[]
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
