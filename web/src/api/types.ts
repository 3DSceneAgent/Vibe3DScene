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

export type StreamEvent = {
  messages?: unknown[]
  todos?: TodoItem[]
  error?: string
}
