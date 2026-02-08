import type { Thread, Settings } from './types'

const DB_NAME = 'sceneAgentDB'
const DB_VERSION = 1
const THREADS_STORE = 'threads'
const SETTINGS_STORE = 'settings'

let dbInstance: IDBDatabase | null = null

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (dbInstance) {
      resolve(dbInstance)
      return
    }

    const request = indexedDB.open(DB_NAME, DB_VERSION)

    request.onerror = () => {
      reject(new Error('Failed to open IndexedDB'))
    }

    request.onsuccess = () => {
      dbInstance = request.result
      resolve(dbInstance)
    }

    request.onupgradeneeded = (event) => {
      const db = (event.target as IDBOpenDBRequest).result

      // Create threads store
      if (!db.objectStoreNames.contains(THREADS_STORE)) {
        db.createObjectStore(THREADS_STORE, { keyPath: 'id' })
      }

      // Create settings store
      if (!db.objectStoreNames.contains(SETTINGS_STORE)) {
        db.createObjectStore(SETTINGS_STORE)
      }
    }
  })
}

export async function saveThreadsToIndexedDB(threads: Thread[]): Promise<void> {
  try {
    const db = await openDatabase()
    const transaction = db.transaction([THREADS_STORE], 'readwrite')
    const store = transaction.objectStore(THREADS_STORE)

    // Clear existing threads
    await new Promise<void>((resolve, reject) => {
      const clearRequest = store.clear()
      clearRequest.onsuccess = () => resolve()
      clearRequest.onerror = () => reject(clearRequest.error)
    })

    // Add all threads
    for (const thread of threads) {
      await new Promise<void>((resolve, reject) => {
        const addRequest = store.put(thread)
        addRequest.onsuccess = () => resolve()
        addRequest.onerror = () => reject(addRequest.error)
      })
    }
  } catch (error) {
    console.error('Failed to save threads to IndexedDB:', error)
    throw error
  }
}

export async function loadThreadsFromIndexedDB(): Promise<Thread[]> {
  try {
    const db = await openDatabase()
    const transaction = db.transaction([THREADS_STORE], 'readonly')
    const store = transaction.objectStore(THREADS_STORE)

    return new Promise((resolve, reject) => {
      const request = store.getAll()
      request.onsuccess = () => {
        const threads = request.result as Thread[]
        resolve(Array.isArray(threads) ? threads : [])
      }
      request.onerror = () => reject(request.error)
    })
  } catch (error) {
    console.error('Failed to load threads from IndexedDB:', error)
    return []
  }
}

export async function saveSettingsToIndexedDB(settings: Settings): Promise<void> {
  try {
    const db = await openDatabase()
    const transaction = db.transaction([SETTINGS_STORE], 'readwrite')
    const store = transaction.objectStore(SETTINGS_STORE)

    await new Promise<void>((resolve, reject) => {
      const request = store.put(settings, 'settings')
      request.onsuccess = () => resolve()
      request.onerror = () => reject(request.error)
    })
  } catch (error) {
    console.error('Failed to save settings to IndexedDB:', error)
    throw error
  }
}

export async function loadSettingsFromIndexedDB(): Promise<Settings | null> {
  try {
    const db = await openDatabase()
    const transaction = db.transaction([SETTINGS_STORE], 'readonly')
    const store = transaction.objectStore(SETTINGS_STORE)

    return new Promise((resolve, reject) => {
      const request = store.get('settings')
      request.onsuccess = () => {
        resolve(request.result ?? null)
      }
      request.onerror = () => reject(request.error)
    })
  } catch (error) {
    console.error('Failed to load settings from IndexedDB:', error)
    return null
  }
}

export function isIndexedDBSupported(): boolean {
  try {
    return 'indexedDB' in window && window.indexedDB !== null
  } catch {
    return false
  }
}
