import type { Message, ToolMedia } from '../state/types'

export type TodoItem = {
  status: 'pending' | 'in_progress' | 'completed' | 'failed' | 'skipped'
  description: string
}

export type AssistantToolCall = {
  id?: string
  name?: string
  key: string
}

export function countConversationMessages(messages: Message[]): number {
  let count = 0
  let currentTurnHasAssistant = false

  for (const message of messages) {
    if (message.role === 'user') {
      count += 1
      currentTurnHasAssistant = false
      continue
    }

    if (message.role !== 'assistant') {
      continue
    }

    if (!currentTurnHasAssistant) {
      count += 1
      currentTurnHasAssistant = true
    }
  }

  return count
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
        } else if (statusRaw === 'skipped') {
          status = 'skipped'
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
      const parsed = JSON.parse(match) as {
        type?: string
        url?: string
        image_url?: { url?: string }
        base64?: string
      }
      if (parsed.type !== 'image') return match
      const directUrl = typeof parsed.url === 'string' ? parsed.url : undefined
      const nestedUrl =
        parsed.image_url && typeof parsed.image_url.url === 'string'
          ? parsed.image_url.url
          : undefined
      const imageUrl = directUrl || nestedUrl
      if (imageUrl && !imageUrl.startsWith('data:')) {
        return `![image](${imageUrl})`
      }
      if (parsed.base64) return '[image data omitted]'
    } catch {
      // If parsing fails, return original match
    }
    return match
  })
}

const TOOL_LIKE_CONTENT_TYPES = new Set([
  'tool_use',
  'tool_call',
  'tool_call_chunk',
  'server_tool_call',
  'server_tool_result',
  'tool_result',
  'function_call',
  'function'
])
const REASONING_LIKE_CONTENT_TYPES = new Set([
  'reasoning',
  'thinking',
  'reasoning_content',
  'summary_text'
])

function hasThoughtSignature(content: unknown): boolean {
  if (!content || typeof content !== 'object') return false
  const maybe = content as {
    thought?: unknown
    thought_signature?: unknown
    signature?: unknown
    extras?: { signature?: unknown }
  }
  if (maybe.thought === true) return true
  if (typeof maybe.thought_signature === 'string' && maybe.thought_signature) return true
  if (typeof maybe.signature === 'string' && maybe.signature) return true
  if (maybe.extras && typeof maybe.extras.signature === 'string' && maybe.extras.signature) return true
  return false
}

function normalizeContentItem(content: unknown): string {
  if (typeof content === 'string') return parseInlineImages(content)
  if (!content || typeof content !== 'object') {
    return content == null ? '' : parseInlineImages(String(content))
  }

  const maybe = content as {
    text?: unknown
    content?: unknown
    content_blocks?: unknown
    value?: unknown
    type?: string
    base64?: string
    url?: string
    image_url?: { url?: string }
  }

  const type = typeof maybe.type === 'string' ? maybe.type.toLowerCase() : ''
  if (TOOL_LIKE_CONTENT_TYPES.has(type)) {
    return ''
  }
  if (REASONING_LIKE_CONTENT_TYPES.has(type) || hasThoughtSignature(content)) {
    return ''
  }

  if (type === 'non_standard' && maybe.value && typeof maybe.value === 'object') {
    const nestedType = (maybe.value as { type?: unknown }).type
    if (typeof nestedType === 'string' && TOOL_LIKE_CONTENT_TYPES.has(nestedType.toLowerCase())) {
      return ''
    }
    return normalizeContent(maybe.value)
  }

  if (type === 'image') {
    const directUrl = typeof maybe.url === 'string' ? maybe.url : undefined
    const nestedUrl =
      maybe.image_url && typeof maybe.image_url.url === 'string'
        ? maybe.image_url.url
        : undefined
    const imageUrl = directUrl || nestedUrl
    if (imageUrl && !imageUrl.startsWith('data:')) {
      return `![image](${imageUrl})`
    }
    if (maybe.base64) {
      return '[image data omitted]'
    }
    return ''
  }

  if (typeof maybe.text === 'string') return maybe.text
  if (typeof maybe.content === 'string') return maybe.content
  if (maybe.content_blocks !== undefined) return normalizeContent(maybe.content_blocks)
  if (maybe.value !== undefined) return normalizeContent(maybe.value)
  return parseInlineImages(JSON.stringify(content))
}

function normalizeThinkingItem(content: unknown): string {
  if (typeof content === 'string') return content
  if (Array.isArray(content)) {
    return content.map((item) => normalizeThinkingItem(item)).filter((item) => item.length > 0).join('')
  }
  if (!content || typeof content !== 'object') return ''

  const maybe = content as {
    type?: string
    thinking?: unknown
    reasoning?: unknown
    text?: unknown
    content?: unknown
    value?: unknown
    extras?: { signature?: unknown }
    additional_kwargs?: { reasoning_content?: unknown; reasoning?: unknown }
  }

  const type = typeof maybe.type === 'string' ? maybe.type.toLowerCase() : ''
  if (REASONING_LIKE_CONTENT_TYPES.has(type) || hasThoughtSignature(content)) {
    if (typeof maybe.thinking === 'string') return maybe.thinking
    if (typeof maybe.reasoning === 'string') return maybe.reasoning
    if (typeof maybe.text === 'string') return maybe.text
    if (typeof maybe.content === 'string') return maybe.content
  }

  if (type === 'non_standard' && maybe.value !== undefined) {
    return normalizeThinkingItem(maybe.value)
  }

  if (maybe.additional_kwargs) {
    if (typeof maybe.additional_kwargs.reasoning_content === 'string') {
      return maybe.additional_kwargs.reasoning_content
    }
    if (maybe.additional_kwargs.reasoning !== undefined) {
      return normalizeThinkingItem(maybe.additional_kwargs.reasoning)
    }
  }

  if (maybe.value !== undefined) return normalizeThinkingItem(maybe.value)
  return ''
}

function normalizeContent(content: unknown): string {
  if (typeof content === 'string') return parseInlineImages(content)
  if (Array.isArray(content)) {
    const parts = content.map((item) => normalizeContentItem(item)).filter((item) => item.length > 0)
    return parseInlineImages(parts.join('\n'))
  }
  if (content && typeof content === 'object') {
    return normalizeContentItem(content)
  }
  return content == null ? '' : parseInlineImages(String(content))
}

export function extractMessageContent(message: unknown): string {
  if (typeof message === 'string') return message
  if (Array.isArray(message)) return normalizeContent(message)
  if (typeof message === 'object' && message !== null) {
    const maybe = message as { content?: unknown; text?: unknown; content_blocks?: unknown }
    if (maybe.content !== undefined) {
      const normalized = normalizeContent(maybe.content)
      if (normalized) return normalized
    }
    if (maybe.text !== undefined) {
      const normalized = normalizeContent(maybe.text)
      if (normalized) return normalized
    }
    if (maybe.content_blocks !== undefined) {
      const normalized = normalizeContent(maybe.content_blocks)
      if (normalized) return normalized
    }
  }
  return normalizeContent(message)
}

export function extractMessageThinking(message: unknown): string {
  if (!message || typeof message !== 'object') return ''
  const maybe = message as {
    reasoning_content?: unknown
    additional_kwargs?: { reasoning_content?: unknown; reasoning?: unknown }
    content?: unknown
    content_blocks?: unknown
  }
  if (typeof maybe.reasoning_content === 'string' && maybe.reasoning_content) {
    return maybe.reasoning_content
  }
  if (maybe.additional_kwargs) {
    if (typeof maybe.additional_kwargs.reasoning_content === 'string' && maybe.additional_kwargs.reasoning_content) {
      return maybe.additional_kwargs.reasoning_content
    }
    if (maybe.additional_kwargs.reasoning !== undefined) {
      const normalized = normalizeThinkingItem(maybe.additional_kwargs.reasoning)
      if (normalized) return normalized
    }
  }
  for (const value of [maybe.content_blocks, maybe.content]) {
    if (value !== undefined) {
      const normalized = normalizeThinkingItem(value)
      if (normalized) return normalized
    }
  }
  return ''
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

function extractToolCallName(value: unknown): string | undefined {
  if (!value || typeof value !== 'object') return undefined
  const maybe = value as {
    name?: unknown
    tool_name?: unknown
    function?: { name?: unknown }
    value?: unknown
  }
  const candidates = [
    maybe.name,
    maybe.tool_name,
    maybe.function && typeof maybe.function === 'object' ? maybe.function.name : undefined
  ]
  for (const candidate of candidates) {
    if (typeof candidate === 'string' && candidate.trim()) {
      return candidate.trim()
    }
  }
  if (maybe.value !== undefined) {
    return extractToolCallName(maybe.value)
  }
  return undefined
}

function extractToolCallId(value: unknown): string | undefined {
  if (!value || typeof value !== 'object') return undefined
  const maybe = value as {
    id?: unknown
    tool_call_id?: unknown
    call_id?: unknown
    value?: unknown
  }
  const candidates = [maybe.id, maybe.tool_call_id, maybe.call_id]
  for (const candidate of candidates) {
    if (typeof candidate === 'string' && candidate.trim()) {
      return candidate.trim()
    }
  }
  if (maybe.value !== undefined) {
    return extractToolCallId(maybe.value)
  }
  return undefined
}

function collectToolCallCandidates(value: unknown, results: unknown[] = []): unknown[] {
  if (Array.isArray(value)) {
    value.forEach((item) => collectToolCallCandidates(item, results))
    return results
  }
  if (!value || typeof value !== 'object') return results

  const maybe = value as {
    type?: unknown
    value?: unknown
    content?: unknown
    content_blocks?: unknown
  }
  const type = typeof maybe.type === 'string' ? maybe.type.toLowerCase() : ''
  if (TOOL_LIKE_CONTENT_TYPES.has(type)) {
    results.push(value)
    return results
  }

  if (maybe.value !== undefined) {
    collectToolCallCandidates(maybe.value, results)
  }
  if (maybe.content !== undefined) {
    collectToolCallCandidates(maybe.content, results)
  }
  if (maybe.content_blocks !== undefined) {
    collectToolCallCandidates(maybe.content_blocks, results)
  }
  return results
}

export function extractAssistantToolCalls(message: unknown): AssistantToolCall[] {
  if (!message || typeof message !== 'object') return []

  const maybe = message as {
    id?: unknown
    tool_calls?: unknown
    tool_call_chunks?: unknown
    additional_kwargs?: { tool_calls?: unknown }
    content?: unknown
    content_blocks?: unknown
  }

  const messageId = typeof maybe.id === 'string' && maybe.id.trim() ? maybe.id.trim() : 'assistant'
  const results: AssistantToolCall[] = []
  const seenKeys = new Set<string>()
  const fallbackCounters = new Map<string, number>()

  const addCall = (value: unknown, source: string) => {
    if (!value || typeof value !== 'object') return
    const name = extractToolCallName(value)
    const id = extractToolCallId(value)
    let key: string
    if (id) {
      key = id
    } else {
      const base = `${messageId}:${source}:${name ?? 'tool'}`
      const count = fallbackCounters.get(base) ?? 0
      fallbackCounters.set(base, count + 1)
      key = `${base}:${count}`
    }
    if (seenKeys.has(key)) return
    seenKeys.add(key)
    results.push({ id, name, key })
  }

  const addCalls = (value: unknown, source: string) => {
    if (!Array.isArray(value)) return
    value.forEach((item) => addCall(item, source))
  }

  addCalls(maybe.tool_calls, 'tool_calls')
  addCalls(maybe.tool_call_chunks, 'tool_call_chunks')
  if (maybe.additional_kwargs && typeof maybe.additional_kwargs === 'object') {
    addCalls(maybe.additional_kwargs.tool_calls, 'additional_kwargs.tool_calls')
  }
  collectToolCallCandidates(maybe.content).forEach((item) => addCall(item, 'content'))
  collectToolCallCandidates(maybe.content_blocks).forEach((item) => addCall(item, 'content_blocks'))
  return results
}

function collectMediaReferences(value: unknown, results: ToolMedia[] = []): ToolMedia[] {
  if (typeof value === 'string') {
    if (
      ((/^https?:\/\//i.test(value) &&
        (/\.(png|jpe?g|gif|webp)(\?|$)/i.test(value) || value.includes('/renders/'))) ||
        /^\/renders\//.test(value))
    ) {
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
      if (typeof entry === 'string' && key.toLowerCase().includes('image')) {
        if (
          ((/^https?:\/\//i.test(entry) &&
            (/\.(png|jpe?g|gif|webp)(\?|$)/i.test(entry) || entry.includes('/renders/'))) ||
            /^\/renders\//.test(entry))
        ) {
          results.push({ kind: 'url', value: entry })
        }
      }
      if (typeof entry === 'string' && (entry.startsWith('data:image/') || key.toLowerCase().includes('base64'))) {
        continue
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
  toolCallId?: string
} | null {
  if (!isToolMessage(message) || typeof message !== 'object' || message === null) return null
  const maybe = message as {
    name?: unknown
    content?: unknown
    tool_call_id?: unknown
    call_id?: unknown
  }
  const payload = maybe.content ?? message
  const name = typeof maybe.name === 'string' ? maybe.name : undefined
  const media = collectMediaReferences(payload)
  const explicitToolCallId =
    typeof maybe.tool_call_id === 'string' && maybe.tool_call_id.trim()
      ? maybe.tool_call_id.trim()
      : typeof maybe.call_id === 'string' && maybe.call_id.trim()
        ? maybe.call_id.trim()
        : undefined
  const nestedToolCallId =
    explicitToolCallId ??
    (payload && typeof payload === 'object'
      ? (() => {
          const maybePayload = payload as { tool_call_id?: unknown; call_id?: unknown }
          if (typeof maybePayload.tool_call_id === 'string' && maybePayload.tool_call_id.trim()) {
            return maybePayload.tool_call_id.trim()
          }
          if (typeof maybePayload.call_id === 'string' && maybePayload.call_id.trim()) {
            return maybePayload.call_id.trim()
          }
          return undefined
        })()
      : undefined)
  return { name, payload, media, toolCallId: nestedToolCallId }
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
