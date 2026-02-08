import type { ToolMedia } from '../state/types'

export type TodoItem = {
  status: 'pending' | 'in_progress' | 'completed' | 'failed'
  description: string
}

export function parseTodos(raw: string): TodoItem[] {
  if (!raw) return []
  const todos: TodoItem[] = []
  const todosPattern = /<todos>([\s\S]*?)<\/todos>/gi
  const matches = raw.matchAll(todosPattern)
  
  for (const match of matches) {
    const content = match[1].trim()
    const lines = content.split('\n')
    
    for (const line of lines) {
      const trimmed = line.trim()
      if (!trimmed) continue
      
      // Match format: - [status] description
      const lineMatch = trimmed.match(/^-?\s*\[(.*?)\]\s*(.*)/)
      if (lineMatch) {
        const statusRaw = lineMatch[1].trim().toLowerCase()
        const description = lineMatch[2].trim()
        
        // Map status variations
        let status: TodoItem['status'] = 'pending'
        if (statusRaw === 'in_progress' || statusRaw === 'in progress') {
          status = 'in_progress'
        } else if (statusRaw === 'completed' || statusRaw === 'done') {
          status = 'completed'
        } else if (statusRaw === 'failed' || statusRaw === 'error') {
          status = 'failed'
        }
        
        if (description) {
          todos.push({ status, description })
        }
      }
    }
  }
  
  return todos
}

export function parseThinking(raw: string): { text: string; thinking?: string } {
  if (!raw) return { text: '' }
  const thinkingChunks: string[] = []
  const thinkingPattern = /<thinking>([\s\S]*?)<\/thinking>/gi
  let text = raw.replace(thinkingPattern, (_, chunk: string) => {
    const cleaned = chunk.trim()
    if (cleaned) thinkingChunks.push(cleaned)
    return ''
  })
  
  // Remove <agent_decision> blocks
  const agentDecisionPattern = /<agent_decision>([\s\S]*?)<\/agent_decision>/gi
  text = text.replace(agentDecisionPattern, '')
  
  // Remove <todos> blocks
  const todosPattern = /<todos>([\s\S]*?)<\/todos>/gi
  text = text.replace(todosPattern, '')
  
  const thinking = thinkingChunks.length > 0 ? thinkingChunks.join('\n\n') : undefined
  return { text: text.trim(), thinking }
}

export function hashString(input: string): number {
  let hash = 0
  for (let i = 0; i < input.length; i += 1) {
    hash = (hash << 5) - hash + input.charCodeAt(i)
    hash |= 0
  }
  return hash
}

function parseInlineImages(text: string): string {
  // Match JSON objects that represent images in LangChain format
  // Pattern: {"type":"image","id":"...","base64":"...","mime_type":"..."}
  const imageJsonPattern = /\{[^{}]*?"type"\s*:\s*"image"[^{}]*?\}/g
  
  return text.replace(imageJsonPattern, (match) => {
    try {
      const parsed = JSON.parse(match)
      if (parsed.type === 'image' && parsed.base64) {
        const mimeType = parsed.mime_type || 'image/png'
        const dataUrl = `data:${mimeType};base64,${parsed.base64}`
        return `![image](${dataUrl})`
      }
    } catch {
      // If parsing fails, return original match
    }
    return match
  })
}

function normalizeContent(content: unknown): string {
  if (typeof content === 'string') return parseInlineImages(content)
  if (Array.isArray(content)) {
    const parts = content.map((item) => {
      if (typeof item === 'string') return item
      if (item && typeof item === 'object') {
        const maybe = item as { text?: unknown; content?: unknown; type?: string; base64?: string; mime_type?: string }
        // Handle image objects directly
        if (maybe.type === 'image' && maybe.base64) {
          const mimeType = maybe.mime_type || 'image/png'
          return `![image](data:${mimeType};base64,${maybe.base64})`
        }
        if (typeof maybe.text === 'string') return maybe.text
        if (typeof maybe.content === 'string') return maybe.content
        return JSON.stringify(item)
      }
      return String(item)
    })
    return parseInlineImages(parts.join('\n'))
  }
  if (content && typeof content === 'object') {
    const maybe = content as { type?: string; base64?: string; mime_type?: string }
    // Handle image objects directly
    if (maybe.type === 'image' && maybe.base64) {
      const mimeType = maybe.mime_type || 'image/png'
      return `![image](data:${mimeType};base64,${maybe.base64})`
    }
    return parseInlineImages(JSON.stringify(content))
  }
  return content == null ? '' : parseInlineImages(String(content))
}

export function extractMessageContent(message: unknown): string {
  if (typeof message === 'string') return message
  if (Array.isArray(message)) return normalizeContent(message)
  if (typeof message === 'object' && message !== null) {
    const maybe = message as { content?: unknown; text?: unknown }
    if (maybe.content !== undefined) return normalizeContent(maybe.content)
    if (maybe.text !== undefined) return normalizeContent(maybe.text)
  }
  return normalizeContent(message)
}

export function isHumanMessage(message: unknown): boolean {
  if (typeof message === 'object' && message !== null) {
    const maybe = message as { type?: unknown; role?: unknown }
    if (maybe.type === 'human' || maybe.role === 'user') return true
  }
  return false
}

export function isToolMessage(message: unknown): boolean {
  if (typeof message === 'object' && message !== null) {
    const maybe = message as { type?: unknown; role?: unknown }
    if (maybe.type === 'tool' || maybe.role === 'tool') return true
  }
  return false
}

function collectMediaReferences(value: unknown, results: ToolMedia[] = []): ToolMedia[] {
  if (typeof value === 'string') {
    if (value.startsWith('data:image/')) {
      results.push({ kind: 'data', value })
    } else if (/^https?:\/\//i.test(value) && /\.(png|jpe?g|gif|webp)$/i.test(value)) {
      results.push({ kind: 'url', value })
    }
    return results
  }

  if (Array.isArray(value)) {
    value.forEach((item) => collectMediaReferences(item, results))
    return results
  }

  if (value && typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>)
    for (const [key, entry] of entries) {
      if (typeof entry === 'string' && key.toLowerCase().includes('image') && !entry.startsWith('data:image/')) {
        if (/^https?:\/\//i.test(entry)) {
          results.push({ kind: 'url', value: entry })
        } else if (/^[A-Za-z0-9+/=]+$/.test(entry) && entry.length > 128) {
          results.push({ kind: 'data', value: `data:image/png;base64,${entry}` })
        }
      }
      collectMediaReferences(entry, results)
    }
  }
  return results
}

export function extractToolPayload(message: unknown): {
  name?: string
  payload: unknown
  media: ToolMedia[]
} | null {
  if (!isToolMessage(message) || typeof message !== 'object' || message === null) return null
  const maybe = message as { name?: unknown; content?: unknown }
  const payload = maybe.content ?? message
  const name = typeof maybe.name === 'string' ? maybe.name : undefined
  const media = collectMediaReferences(payload)
  return { name, payload, media }
}

export function isAssistantMessage(message: unknown): boolean {
  if (typeof message === 'object' && message !== null) {
    const maybe = message as { type?: unknown; role?: unknown }
    if (maybe.type === 'ai' || maybe.role === 'assistant') return true
  }
  return false
}

export function applyStreamingDelta(raw: string | undefined, delta: string): {
  raw: string
  text: string
  thinking?: string
} {
  const nextRaw = `${raw ?? ''}${delta}`
  const parsed = parseThinking(nextRaw)
  return { raw: nextRaw, text: parsed.text, thinking: parsed.thinking }
}

export function applyStreamingDeltaWithId(
  raw: string | undefined,
  delta: string,
  currentMessageId: string | null | undefined,
  nextMessageId: string | null | undefined
): {
  raw: string
  text: string
  thinking?: string
  messageId: string | null
} {
  const incomingId = nextMessageId ?? currentMessageId ?? null
  const shouldReset = nextMessageId != null && nextMessageId !== currentMessageId
  const nextRaw = shouldReset ? delta : `${raw ?? ''}${delta}`
  const parsed = parseThinking(nextRaw)
  return { raw: nextRaw, text: parsed.text, thinking: parsed.thinking, messageId: incomingId }
}
