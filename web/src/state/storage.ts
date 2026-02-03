import type { Settings, Thread } from './types'

const THREADS_KEY = 'sceneAgentThreads'
const SETTINGS_KEY = 'sceneAgentSettings'

export const defaultSettings: Settings = {
  backendUrl: 'http://localhost:8000',
  theme: 'dark',
  autoRefreshScene: false,
  sceneTabCollapsed: false
}

const legacyDarkThemes = new Set(['midnight', 'slate', 'warm'])

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
    gltfUrl: null,
    sceneHasChange: false
  }))
  localStorage.setItem(THREADS_KEY, JSON.stringify(sanitized))
}

export function loadSettings(): Settings {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY)
    if (!raw) return defaultSettings
    const parsed = JSON.parse(raw) as Settings
    const nextTheme =
      parsed.theme && legacyDarkThemes.has(parsed.theme)
        ? 'dark'
        : parsed.theme || defaultSettings.theme
    return {
      backendUrl: parsed.backendUrl || defaultSettings.backendUrl,
      theme: nextTheme,
      autoRefreshScene: parsed.autoRefreshScene ?? defaultSettings.autoRefreshScene,
      sceneTabCollapsed: parsed.sceneTabCollapsed ?? defaultSettings.sceneTabCollapsed
    }
  } catch {
    return defaultSettings
  }
}

export function saveSettings(settings: Settings) {
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings))
}
