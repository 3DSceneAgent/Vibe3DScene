import type { Settings, Thread } from './types'

const THREADS_KEY = 'sceneAgentThreads'
const SETTINGS_KEY = 'sceneAgentSettings'

export const defaultSettings: Settings = {
  backendUrl: 'http://localhost:8000',
  theme: 'midnight'
}

export function loadThreads(): Thread[] {
  try {
    const raw = localStorage.getItem(THREADS_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as Thread[]
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

export function saveThreads(threads: Thread[]) {
  const sanitized = threads.map((thread) => ({
    ...thread,
    renders: [],
    gltfUrl: null
  }))
  localStorage.setItem(THREADS_KEY, JSON.stringify(sanitized))
}

export function loadSettings(): Settings {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY)
    if (!raw) return defaultSettings
    const parsed = JSON.parse(raw) as Settings
    return {
      backendUrl: parsed.backendUrl || defaultSettings.backendUrl,
      theme: parsed.theme || defaultSettings.theme
    }
  } catch {
    return defaultSettings
  }
}

export function saveSettings(settings: Settings) {
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings))
}
