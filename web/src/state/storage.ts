import type { Settings, Thread } from './types'
import {
  isIndexedDBSupported,
  loadThreadsFromIndexedDB,
  saveThreadsToIndexedDB,
  loadSettingsFromIndexedDB,
  saveSettingsToIndexedDB
} from './indexeddb'

const THREADS_KEY = 'sceneAgentThreads'
const SETTINGS_KEY = 'sceneAgentSettings'
const MAX_MESSAGES_PER_THREAD = 100

export const defaultSettings: Settings = {
  backendUrl: 'http://localhost:8000',
  theme: 'dark',
  autoRefreshScene: true,
  viewportTheme: 'auto'
}

const legacyDarkThemes = new Set(['midnight', 'slate', 'warm'])
const viewportThemes = new Set<Settings['viewportTheme']>(['auto', 'dark', 'light'])
const useIndexedDB = isIndexedDBSupported()

function sanitizeThreads(threads: Thread[]): Thread[] {
  return threads.map((thread) => {
    // Limit messages per thread to avoid storage overflow
    const messages = thread.messages.slice(-MAX_MESSAGES_PER_THREAD)
    
    return {
      ...thread,
      messages,
      renders: [],
      gltfUrl: null,
      sceneHierarchy: [],
      sceneHasChange: false,
      referenceImages:
        thread.referenceImages?.map((image) => {
          const sanitizedImage = { ...image }
          delete sanitizedImage.previewUrl
          return sanitizedImage
        }) ?? []
    }
  })
}

function loadThreadsFromLocalStorage(): Thread[] {
  try {
    const raw = localStorage.getItem(THREADS_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as Thread[]
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function saveThreadsToLocalStorage(threads: Thread[]): boolean {
  const sanitized = sanitizeThreads(threads)
  try {
    localStorage.setItem(THREADS_KEY, JSON.stringify(sanitized))
    return true
  } catch (error) {
    if (error instanceof DOMException && error.name === 'QuotaExceededError') {
      console.error('LocalStorage quota exceeded. Attempting to clean up...')
      try {
        // Try to save only the most recent threads
        const recentThreads = sanitized.slice(-5)
        localStorage.setItem(THREADS_KEY, JSON.stringify(recentThreads))
        console.warn('Saved only the 5 most recent threads due to storage constraints')
        return true
      } catch {
        console.error('Failed to save even after cleanup')
        return false
      }
    }
    console.error('Failed to save threads to localStorage:', error)
    return false
  }
}

export function loadThreads(): Thread[] {
  if (useIndexedDB) {
    // IndexedDB is async, but we need to provide a sync API for initial load
    // So we return empty and trigger async load separately
    return []
  }
  return loadThreadsFromLocalStorage()
}

export async function loadThreadsAsync(): Promise<Thread[]> {
  if (useIndexedDB) {
    try {
      const threads = await loadThreadsFromIndexedDB()
      if (threads.length > 0) {
        return threads
      }
      // Try to migrate from localStorage if IndexedDB is empty
      const localThreads = loadThreadsFromLocalStorage()
      if (localThreads.length > 0) {
        console.log('Migrating threads from localStorage to IndexedDB...')
        await saveThreadsToIndexedDB(sanitizeThreads(localThreads))
        // Clear localStorage after successful migration
        try {
          localStorage.removeItem(THREADS_KEY)
        } catch {
          // Ignore errors when clearing localStorage
        }
        return localThreads
      }
      return []
    } catch (error) {
      console.error('Failed to load from IndexedDB, falling back to localStorage:', error)
      return loadThreadsFromLocalStorage()
    }
  }
  return loadThreadsFromLocalStorage()
}

export function saveThreads(threads: Thread[]) {
  if (useIndexedDB) {
    const sanitized = sanitizeThreads(threads)
    saveThreadsToIndexedDB(sanitized).catch((error) => {
      console.error('Failed to save to IndexedDB, falling back to localStorage:', error)
      saveThreadsToLocalStorage(threads)
    })
  } else {
    saveThreadsToLocalStorage(threads)
  }
}

function loadSettingsFromLocalStorage(): Settings {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY)
    if (!raw) return defaultSettings
    const parsed = JSON.parse(raw) as Partial<Settings> & { sceneTabCollapsed?: boolean }
    return normalizeSettings(parsed)
  } catch {
    return defaultSettings
  }
}

function normalizeSettings(settings: Partial<Settings> | null | undefined): Settings {
  const rawTheme = settings?.theme
  const nextTheme =
    rawTheme && legacyDarkThemes.has(rawTheme)
      ? 'dark'
      : rawTheme === 'dark' || rawTheme === 'light'
        ? rawTheme
        : defaultSettings.theme
  const rawViewportTheme = settings?.viewportTheme
  const nextViewportTheme =
    typeof rawViewportTheme === 'string' && viewportThemes.has(rawViewportTheme as Settings['viewportTheme'])
      ? (rawViewportTheme as Settings['viewportTheme'])
      : defaultSettings.viewportTheme

  return {
    backendUrl: settings?.backendUrl || defaultSettings.backendUrl,
    theme: nextTheme,
    autoRefreshScene: settings?.autoRefreshScene ?? defaultSettings.autoRefreshScene,
    viewportTheme: nextViewportTheme
  }
}

function saveSettingsToLocalStorage(settings: Settings): boolean {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings))
    return true
  } catch (error) {
    console.error('Failed to save settings to localStorage:', error)
    return false
  }
}

export function loadSettings(): Settings {
  if (useIndexedDB) {
    // Return default settings initially, async load will update later
    return defaultSettings
  }
  return loadSettingsFromLocalStorage()
}

export async function loadSettingsAsync(): Promise<Settings> {
  if (useIndexedDB) {
    try {
      const settings = await loadSettingsFromIndexedDB()
      if (settings) {
        return normalizeSettings(settings)
      }
      // Try to migrate from localStorage if IndexedDB is empty
      const localSettings = loadSettingsFromLocalStorage()
      if (localSettings !== defaultSettings) {
        console.log('Migrating settings from localStorage to IndexedDB...')
        await saveSettingsToIndexedDB(localSettings)
        // Clear localStorage after successful migration
        try {
          localStorage.removeItem(SETTINGS_KEY)
        } catch {
          // Ignore errors when clearing localStorage
        }
      }
      return localSettings
    } catch (error) {
      console.error('Failed to load settings from IndexedDB, falling back to localStorage:', error)
      return loadSettingsFromLocalStorage()
    }
  }
  return loadSettingsFromLocalStorage()
}

export function saveSettings(settings: Settings) {
  if (useIndexedDB) {
    saveSettingsToIndexedDB(settings).catch((error) => {
      console.error('Failed to save settings to IndexedDB, falling back to localStorage:', error)
      saveSettingsToLocalStorage(settings)
    })
  } else {
    saveSettingsToLocalStorage(settings)
  }
}
