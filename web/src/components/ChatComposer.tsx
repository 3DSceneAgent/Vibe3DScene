import { useEffect, useRef, useState } from 'react'
import type {
  PendingImageAttachment,
  SceneObjectReference,
  SceneObjectReferenceInsertion
} from '../state/types'
import { SceneObjectIcon } from './SceneObjectIcon'

type ModelOption = {
  value: string
  label: string
}

type ChatComposerProps = {
  disabled?: boolean
  onSend: (
    message: string,
    images: PendingImageAttachment[],
    referencedObjects: SceneObjectReference[]
  ) => Promise<boolean>
  onStop?: () => void
  referenceImagesCount?: number
  examplePrompts?: string[]
  promptHistory?: string[]
  mcpTools?: string[]
  mcpToolHints?: Record<string, string>
  mcpToolEnabled?: Record<string, boolean>
  onMcpToolToggle?: (toolName: string, enabled: boolean) => void
  mcpToolsLoading?: boolean
  mcpToolsError?: string | null
  modelOptions?: ModelOption[]
  selectedModelValue?: string
  onModelSelectionChange?: (value: string) => void
  providerThinking?: boolean
  providerThinkingAvailable?: boolean
  onProviderThinkingToggle?: (enabled: boolean) => void
  fastMode?: boolean
  onFastModeToggle?: (enabled: boolean) => void
  fastModeAvailable?: boolean
  modelLoading?: boolean
  modelError?: string | null
  modelLocked?: boolean
  showModelSelector?: boolean
  showMcpTools?: boolean
  showFastMode?: boolean
  runtimeHint?: string | null
  availableReferences?: SceneObjectReference[]
  pendingReferenceInsertion?: SceneObjectReferenceInsertion | null
  onPendingReferenceInsertionHandled?: (key: string) => void
}

const MAX_REFERENCE_IMAGES = 3
const MAX_EXAMPLE_PROMPTS = 10
const MAX_HISTORY_PROMPTS = 8
const MAX_MENTION_RESULTS = 8

type ReferencedObjectEntry = SceneObjectReference & {
  instanceKey: string
}

type MentionContext = {
  start: number
  end: number
  query: string
}

function createReferencedObjectEntry(reference: SceneObjectReference): ReferencedObjectEntry {
  return {
    ...reference,
    instanceKey: `${reference.backendObjectId}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  }
}

function findActiveMention(input: string, caretPosition: number): MentionContext | null {
  if (caretPosition < 0 || caretPosition > input.length) {
    return null
  }

  const prefix = input.slice(0, caretPosition)
  const match = prefix.match(/(?:^|[\s([{])@([^\s@]*)$/)
  if (!match) {
    return null
  }

  const query = match[1] ?? ''
  return {
    start: caretPosition - query.length - 1,
    end: caretPosition,
    query
  }
}

function rankReferenceMatch(reference: SceneObjectReference, query: string): number {
  const loweredQuery = query.trim().toLowerCase()
  if (!loweredQuery) {
    return 0
  }
  const displayName = reference.displayName.toLowerCase()
  if (displayName === loweredQuery) {
    return 0
  }
  if (displayName.startsWith(loweredQuery)) {
    return 1
  }
  if (displayName.includes(loweredQuery)) {
    return 2
  }
  if (reference.backendObjectId.toLowerCase().includes(loweredQuery)) {
    return 3
  }
  return Number.POSITIVE_INFINITY
}

export function ChatComposer({
  disabled,
  onSend,
  onStop,
  referenceImagesCount = 0,
  examplePrompts = [],
  promptHistory = [],
  mcpTools = [],
  mcpToolHints = {},
  mcpToolEnabled = {},
  onMcpToolToggle,
  mcpToolsLoading = false,
  mcpToolsError = null,
  modelOptions = [],
  selectedModelValue = '',
  onModelSelectionChange,
  providerThinking = false,
  providerThinkingAvailable = false,
  onProviderThinkingToggle,
  fastMode = false,
  onFastModeToggle,
  fastModeAvailable = false,
  modelLoading = false,
  modelError = null,
  modelLocked = false,
  showModelSelector = true,
  showMcpTools = true,
  showFastMode = true,
  runtimeHint = null,
  availableReferences = [],
  pendingReferenceInsertion = null,
  onPendingReferenceInsertionHandled
}: ChatComposerProps) {
  const [input, setInput] = useState('')
  const [caretPosition, setCaretPosition] = useState(0)
  const [dragActive, setDragActive] = useState(false)
  const [isInputFocused, setIsInputFocused] = useState(false)
  const [isToolsOpen, setIsToolsOpen] = useState(false)
  const [toolSearchQuery, setToolSearchQuery] = useState('')
  const [pendingImages, setPendingImages] = useState<PendingImageAttachment[]>([])
  const [referencedObjects, setReferencedObjects] = useState<ReferencedObjectEntry[]>([])
  const [mentionActiveIndex, setMentionActiveIndex] = useState(0)
  const [dismissedMentionSignature, setDismissedMentionSignature] = useState<string | null>(null)
  const blurTimeoutRef = useRef<number | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const toolsPanelRef = useRef<HTMLDivElement | null>(null)
  const pendingImagesRef = useRef<PendingImageAttachment[]>([])
  const lastReferenceInsertionKeyRef = useRef<string | null>(null)
  const pendingReferenceFrameRef = useRef<number | null>(null)

  const insertReferenceAtCursor = (
    reference: SceneObjectReference,
    mentionContext: MentionContext | null
  ) => {
    const textarea = textareaRef.current
    const selectionStart = textarea?.selectionStart ?? caretPosition
    const selectionEnd = textarea?.selectionEnd ?? selectionStart
    const replacementStart = mentionContext?.start ?? selectionStart
    const replacementEnd = mentionContext?.end ?? selectionEnd
    const insertionText = `@${reference.displayName} `
    const nextInput =
      input.slice(0, replacementStart) +
      insertionText +
      input.slice(replacementEnd)
    const nextCaretPosition = replacementStart + insertionText.length
    setInput(nextInput)
    setCaretPosition(nextCaretPosition)
    setDismissedMentionSignature(null)
    window.requestAnimationFrame(() => {
      textareaRef.current?.focus()
      textareaRef.current?.setSelectionRange(nextCaretPosition, nextCaretPosition)
    })
  }

  const handleReferenceSelected = (
    reference: SceneObjectReference,
    mentionContext: MentionContext | null
  ) => {
    setReferencedObjects((current) => [...current, createReferencedObjectEntry(reference)])
    insertReferenceAtCursor(reference, mentionContext)
  }

  const closeToolsPanel = () => {
    setIsToolsOpen(false)
    setToolSearchQuery('')
  }

  useEffect(() => {
    pendingImagesRef.current = pendingImages
  }, [pendingImages])

  useEffect(() => {
    return () => {
      pendingImagesRef.current.forEach((image) => URL.revokeObjectURL(image.previewUrl))
      if (pendingReferenceFrameRef.current !== null) {
        window.cancelAnimationFrame(pendingReferenceFrameRef.current)
      }
      if (blurTimeoutRef.current !== null) {
        window.clearTimeout(blurTimeoutRef.current)
      }
    }
  }, [])

  useEffect(() => {
    if (!pendingReferenceInsertion) {
      return
    }
    if (lastReferenceInsertionKeyRef.current === pendingReferenceInsertion.key) {
      return
    }
    lastReferenceInsertionKeyRef.current = pendingReferenceInsertion.key
    const reference = pendingReferenceInsertion.reference
    pendingReferenceFrameRef.current = window.requestAnimationFrame(() => {
      pendingReferenceFrameRef.current = null
      setReferencedObjects((current) => [...current, createReferencedObjectEntry(reference)])
      const textarea = textareaRef.current
      const selectionStart = textarea?.selectionStart ?? caretPosition
      const selectionEnd = textarea?.selectionEnd ?? selectionStart
      const insertionText = `@${reference.displayName} `
      const nextInput = input.slice(0, selectionStart) + insertionText + input.slice(selectionEnd)
      const nextCaretPosition = selectionStart + insertionText.length
      setInput(nextInput)
      setCaretPosition(nextCaretPosition)
      setDismissedMentionSignature(null)
      textareaRef.current?.focus()
      textareaRef.current?.setSelectionRange(nextCaretPosition, nextCaretPosition)
      onPendingReferenceInsertionHandled?.(pendingReferenceInsertion.key)
    })
    return () => {
      if (pendingReferenceFrameRef.current !== null) {
        window.cancelAnimationFrame(pendingReferenceFrameRef.current)
        pendingReferenceFrameRef.current = null
      }
    }
  }, [caretPosition, input, onPendingReferenceInsertionHandled, pendingReferenceInsertion])

  useEffect(() => {
    if (!isToolsOpen) return

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node | null
      if (toolsPanelRef.current?.contains(target)) {
        return
      }
      closeToolsPanel()
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        closeToolsPanel()
      }
    }

    window.addEventListener('pointerdown', handlePointerDown)
    window.addEventListener('keydown', handleKeyDown)
    return () => {
      window.removeEventListener('pointerdown', handlePointerDown)
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [isToolsOpen])

  const handleSend = async () => {
    const text = input.trim()
    if (!text) return
    const submittedImages = pendingImages
    const submittedReferences = referencedObjects.map((reference) => ({
      backendObjectId: reference.backendObjectId,
      displayName: reference.displayName,
      objectType: reference.objectType
    }))
    setInput('')
    setCaretPosition(0)
    setPendingImages([])
    setReferencedObjects([])
    setDismissedMentionSignature(null)
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
    const success = await onSend(text, submittedImages, submittedReferences)
    if (success) {
      return
    }
  }

  const addFiles = (files: File[]) => {
    if (files.length === 0) return
    const remaining = Math.max(0, MAX_REFERENCE_IMAGES - referenceImagesCount - pendingImages.length)
    if (remaining === 0) return
    const selection = files.filter((file) => file.type.startsWith('image/')).slice(0, remaining)
    if (selection.length === 0) return
    setPendingImages((prev) => [
      ...prev,
      ...selection.map((file) => ({ file, previewUrl: URL.createObjectURL(file) }))
    ])
  }

  const handleFiles = (files: FileList | null) => {
    if (!files || files.length === 0) return
    addFiles(Array.from(files))
  }

  const handleDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    event.stopPropagation()
    setDragActive(false)
    if (disabled) return
    addFiles(Array.from(event.dataTransfer.files))
  }

  const handleDragOver = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    event.stopPropagation()
    if (!disabled) {
      setDragActive(true)
    }
  }

  const handleDragLeave = () => setDragActive(false)

  const handlePaste = (event: React.ClipboardEvent<HTMLTextAreaElement>) => {
    if (disabled) return
    const imageFiles = Array.from(event.clipboardData.items)
      .filter((item) => item.kind === 'file' && item.type.startsWith('image/'))
      .map((item) => item.getAsFile())
      .filter((file): file is File => file !== null)
    if (imageFiles.length === 0) {
      return
    }
    event.preventDefault()
    addFiles(imageFiles)
  }

  const handleRemovePending = (index: number) => {
    setPendingImages((prev) => {
      const next = [...prev]
      const [removed] = next.splice(index, 1)
      if (removed?.previewUrl) {
        URL.revokeObjectURL(removed.previewUrl)
      }
      return next
    })
  }

  const handleSelectPrompt = (prompt: string) => {
    setInput(prompt)
    setCaretPosition(prompt.length)
    textareaRef.current?.focus()
  }

  const handleRemoveReferencedObject = (instanceKey: string) => {
    setReferencedObjects((current) => current.filter((entry) => entry.instanceKey !== instanceKey))
  }

  const handleInputFocus = () => {
    if (blurTimeoutRef.current !== null) {
      window.clearTimeout(blurTimeoutRef.current)
      blurTimeoutRef.current = null
    }
    setIsInputFocused(true)
    setCaretPosition(textareaRef.current?.selectionStart ?? input.length)
  }

  const handleInputBlur = () => {
    if (blurTimeoutRef.current !== null) {
      window.clearTimeout(blurTimeoutRef.current)
    }
    blurTimeoutRef.current = window.setTimeout(() => {
      setIsInputFocused(false)
      blurTimeoutRef.current = null
    }, 120)
  }

  const historyOptions = promptHistory.slice(0, MAX_HISTORY_PROMPTS)
  const historyPromptSet = new Set(historyOptions)
  const promptOptions = examplePrompts
    .filter((prompt) => !historyPromptSet.has(prompt))
    .slice(0, MAX_EXAMPLE_PROMPTS)
  const hasPendingImages = pendingImages.length > 0
  const hasReferencedObjects = referencedObjects.length > 0
  const mentionContext = findActiveMention(input, caretPosition)
  const mentionSignature = mentionContext
    ? `${mentionContext.start}:${mentionContext.end}:${mentionContext.query}`
    : null
  const mentionOptions = mentionContext
    ? availableReferences
        .map((reference) => ({
          reference,
          rank: rankReferenceMatch(reference, mentionContext.query)
        }))
        .filter((entry) => Number.isFinite(entry.rank))
        .sort((left, right) => {
          if (left.rank !== right.rank) {
            return left.rank - right.rank
          }
          return left.reference.displayName.localeCompare(right.reference.displayName)
        })
        .slice(0, MAX_MENTION_RESULTS)
        .map((entry) => entry.reference)
    : []
  const showMentionPopover =
    isInputFocused &&
    Boolean(mentionContext) &&
    mentionOptions.length > 0 &&
    mentionSignature !== dismissedMentionSignature
  const showPromptPopover =
    isInputFocused &&
    !showMentionPopover &&
    !hasReferencedObjects &&
    input.trim().length === 0 &&
    (historyOptions.length > 0 || promptOptions.length > 0)
  const modelSelectDisabled = modelLocked || modelLoading || modelOptions.length === 0
  const activeModelOption =
    modelOptions.find((option) => option.value === selectedModelValue) ??
    (modelOptions.length > 0 ? modelOptions[0] : null)
  const activeModelLabel = activeModelOption?.label ?? 'No model available'
  const modelSelectWidthCh = Math.max(10, activeModelLabel.length)
  const canSend = input.trim().length > 0
  const enabledToolCount = mcpTools.filter((toolName) => mcpToolEnabled[toolName] !== false).length
  const normalizedToolSearchQuery = toolSearchQuery.trim().toLowerCase()
  const filteredMcpTools = normalizedToolSearchQuery
    ? mcpTools.filter((toolName) => toolName.toLowerCase().includes(normalizedToolSearchQuery))
    : mcpTools
  const fastModeDisabled = Boolean(disabled)
  const resolvedMentionActiveIndex = mentionOptions[mentionActiveIndex] ? mentionActiveIndex : 0

  const modelMetaTitle = modelLoading
    ? 'Loading model options...'
    : modelLocked
      ? 'Model switch is currently locked'
      : modelError ?? undefined

  const shortenFilename = (filename: string) => {
    const stem = filename.replace(/\.[^/.]+$/, '')
    return stem.slice(0, 10)
  }

  const resolveToolHint = (toolName: string) => {
    const hint = mcpToolHints[toolName]?.trim()
    if (!hint || hint === `MCP tool: ${toolName}`) {
      return null
    }
    return hint
  }

  return (
    <div className="composer-shell">
      <div
        className={`composer-input-panel ${dragActive ? 'drag-active' : ''} ${hasPendingImages ? 'has-images' : ''} ${hasReferencedObjects ? 'has-object-refs' : ''}`}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
      >
        {showMentionPopover && mentionContext && (
          <div className="composer-mention-popover" role="listbox" aria-label="Scene object suggestions">
            <div className="composer-mention-section-label">Scene objects</div>
            {mentionOptions.map((reference, index) => (
              <button
                key={reference.backendObjectId}
                type="button"
                className={`composer-mention-option ${index === resolvedMentionActiveIndex ? 'is-active' : ''}`}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => handleReferenceSelected(reference, mentionContext)}
                title={reference.displayName}
                disabled={disabled}
              >
                <span className="composer-mention-option-icon" aria-hidden="true">
                  <SceneObjectIcon
                    type={reference.objectType ?? 'OBJECT3D'}
                    className="composer-object-ref-icon-svg"
                  />
                </span>
                <span className="composer-mention-option-label">{reference.displayName}</span>
              </button>
            ))}
          </div>
        )}
        {showPromptPopover && (
          <div className="composer-prompt-popover" role="listbox" aria-label="Prompt suggestions">
            {historyOptions.length > 0 && (
              <div className="composer-prompt-section">
                <div className="composer-prompt-section-label">Recent prompts</div>
                {historyOptions.map((prompt, index) => (
                  <button
                    key={`history-${index}-${prompt}`}
                    type="button"
                    className="composer-prompt-option"
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => handleSelectPrompt(prompt)}
                    title={prompt}
                    disabled={disabled}
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            )}
            {promptOptions.length > 0 && (
              <div className="composer-prompt-section">
                <div className="composer-prompt-section-label">Examples</div>
                {promptOptions.map((prompt, index) => (
                  <button
                    key={`example-${index}-${prompt}`}
                    type="button"
                    className="composer-prompt-option"
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => handleSelectPrompt(prompt)}
                    title={prompt}
                    disabled={disabled}
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
        {hasPendingImages && (
          <div className="composer-inline-attachments">
            {pendingImages.map((image, index) => (
              <div className="composer-inline-attachment" key={`${image.file.name}-${index}`}>
                <img src={image.previewUrl} alt={image.file.name} />
                <div className="composer-inline-file" title={image.file.name}>
                  {shortenFilename(image.file.name)}
                </div>
                <button
                  className="composer-inline-remove"
                  type="button"
                  onClick={() => handleRemovePending(index)}
                  aria-label={`Remove ${image.file.name}`}
                >
                  x
                </button>
              </div>
            ))}
          </div>
        )}
        {hasReferencedObjects && (
          <div className="composer-object-refs" aria-label="Referenced scene objects">
            {referencedObjects.map((reference) => (
              <div key={reference.instanceKey} className="composer-object-ref-chip">
                <span className="composer-object-ref-icon" aria-hidden="true">
                  <SceneObjectIcon
                    type={reference.objectType ?? 'OBJECT3D'}
                    className="composer-object-ref-icon-svg"
                  />
                </span>
                <span className="composer-object-ref-label" title={reference.displayName}>
                  {reference.displayName}
                </span>
                <button
                  className="composer-object-ref-remove"
                  type="button"
                  onClick={() => handleRemoveReferencedObject(reference.instanceKey)}
                  aria-label={`Remove ${reference.displayName}`}
                >
                  x
                </button>
              </div>
            ))}
          </div>
        )}
        <textarea
          ref={textareaRef}
          className="composer-input"
          placeholder="Ask the scene agent..."
          value={input}
          disabled={disabled}
          onFocus={handleInputFocus}
          onBlur={handleInputBlur}
          onPaste={handlePaste}
          onSelect={(event) => setCaretPosition(event.currentTarget.selectionStart ?? 0)}
          onChange={(event) => {
            setInput(event.target.value)
            setCaretPosition(event.target.selectionStart ?? event.target.value.length)
          }}
          onKeyDown={(event) => {
            if (showMentionPopover) {
              if (event.key === 'ArrowDown') {
                event.preventDefault()
                setMentionActiveIndex((current) => (current + 1) % mentionOptions.length)
                return
              }
              if (event.key === 'ArrowUp') {
                event.preventDefault()
                setMentionActiveIndex((current) =>
                  (current - 1 + mentionOptions.length) % mentionOptions.length
                )
                return
              }
              if (event.key === 'Escape') {
                event.preventDefault()
                setDismissedMentionSignature(mentionSignature)
                return
              }
              if (event.key === 'Enter' && !event.ctrlKey && !event.metaKey && !event.shiftKey) {
                event.preventDefault()
                const selection = mentionOptions[resolvedMentionActiveIndex] ?? mentionOptions[0]
                if (selection) {
                  handleReferenceSelected(selection, mentionContext)
                }
                return
              }
            }
            if (event.key !== 'Enter') return
            if (event.ctrlKey || event.metaKey || event.shiftKey) {
              return
            }
            event.preventDefault()
            void handleSend()
          }}
          rows={1}
        />
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          multiple
          hidden
          onChange={(event) => handleFiles(event.target.files)}
        />
        <div className="composer-input-footer">
          <div className="composer-input-footer-left">
            <button
              type="button"
              className="composer-attach-btn"
              onClick={() => {
                if (disabled) return
                fileInputRef.current?.click()
              }}
              disabled={disabled || referenceImagesCount + pendingImages.length >= MAX_REFERENCE_IMAGES}
              aria-label="Add images"
              title="Attach image"
            >
              <span className="composer-attach-btn-icon" aria-hidden="true">
                +
              </span>
            </button>
            {showModelSelector && (
              <select
                id="composer-model-select"
                className="composer-model-select"
                value={selectedModelValue}
                disabled={modelSelectDisabled}
                onChange={(event) => onModelSelectionChange?.(event.target.value)}
                title={runtimeHint ?? modelMetaTitle}
                style={{ width: `${modelSelectWidthCh}ch` }}
              >
                {modelOptions.length === 0 && <option value="">No model available</option>}
                {modelOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            )}
            {providerThinkingAvailable && (
              <div
                className={`composer-fast-mode-chip ${disabled ? 'is-disabled' : ''}`}
                title="Provider thinking: include hidden reasoning summaries when supported"
              >
                <div className="composer-fast-mode-chip-copy">
                  <div className="composer-fast-mode-chip-title">Thinking</div>
                </div>
                <label
                  className="toggle-switch compact composer-fast-mode-toggle"
                  aria-label="Toggle provider thinking"
                  title="Provider thinking: include hidden reasoning summaries when supported"
                >
                  <input
                    type="checkbox"
                    checked={providerThinking}
                    onChange={(event) => onProviderThinkingToggle?.(event.target.checked)}
                    disabled={Boolean(disabled)}
                  />
                  <span className="toggle-slider" />
                </label>
              </div>
            )}
            {showMcpTools && (
              <div
                ref={toolsPanelRef}
                className={`composer-tools-panel ${isToolsOpen ? 'open' : ''}`}
                onBlur={(event) => {
                  if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
                    closeToolsPanel()
                  }
                }}
              >
                <button
                  type="button"
                  className="composer-tools-summary"
                  onClick={() => {
                    if (isToolsOpen) {
                      closeToolsPanel()
                      return
                    }
                    setIsToolsOpen(true)
                  }}
                  aria-expanded={isToolsOpen}
                  aria-haspopup="true"
                >
                  <span className="composer-tools-summary-copy">
                    <span className="composer-tools-title">Tools</span>
                    <span className="composer-tools-badge">
                      {enabledToolCount}/{mcpTools.length}
                    </span>
                  </span>
                  {mcpToolsLoading && <span className="composer-tools-meta">...</span>}
                  {!mcpToolsLoading && mcpToolsError && (
                    <span className="composer-tools-meta error">!</span>
                  )}
                  <span className="composer-tools-caret" aria-hidden="true" />
                </button>
                {isToolsOpen && (
                  <div className="composer-tools-body">
                    <div className="composer-tools-body-header">
                      <div className="composer-tools-body-title">Per-thread tool access</div>
                      {mcpTools.length > 0 && (
                        <input
                          type="search"
                          className="composer-tools-search"
                          placeholder="Search tools"
                          value={toolSearchQuery}
                          onChange={(event) => setToolSearchQuery(event.target.value)}
                          autoFocus
                          spellCheck={false}
                          aria-label="Search per-thread tools"
                        />
                      )}
                    </div>
                    {mcpTools.length > 0 ? (
                      filteredMcpTools.length > 0 ? (
                        <ul className="composer-tools-list">
                          {filteredMcpTools.map((toolName) => {
                            const hint = resolveToolHint(toolName)
                            return (
                              <li key={toolName} className="composer-tools-item">
                                <label className="composer-tools-toggle" title={hint ?? undefined}>
                                  <span className="composer-tools-name">{toolName}</span>
                                  <span className="toggle-switch compact composer-tools-switch">
                                    <input
                                      type="checkbox"
                                      checked={mcpToolEnabled[toolName] !== false}
                                      onChange={(event) =>
                                        onMcpToolToggle?.(toolName, event.target.checked)
                                      }
                                      disabled={disabled || mcpToolsLoading}
                                    />
                                    <span className="toggle-slider" />
                                  </span>
                                </label>
                              </li>
                            )
                          })}
                        </ul>
                      ) : (
                        <div className="composer-tools-empty muted">
                          No tools match "{toolSearchQuery.trim()}".
                        </div>
                      )
                    ) : (
                      <div className="composer-tools-empty muted">
                        {mcpToolsLoading
                          ? 'Connecting to MCP server and loading tools...'
                          : mcpToolsError
                            ? 'No MCP tools available for this thread.'
                            : 'No MCP tools loaded yet.'}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
          <div className="composer-input-footer-right">
            {showFastMode && fastModeAvailable && (
              <div
                className={`composer-fast-mode-chip ${fastModeDisabled ? 'is-disabled' : ''}`}
                title="Fast mode: skip auto observe and verify"
              >
                <div className="composer-fast-mode-chip-copy">
                  <div className="composer-fast-mode-chip-title">Fast mode</div>
                </div>
                <label
                  className="toggle-switch compact composer-fast-mode-toggle"
                  aria-label="Toggle fast mode"
                  title="Fast mode: skip auto observe and verify"
                >
                  <input
                    type="checkbox"
                    checked={fastMode}
                    onChange={(event) => onFastModeToggle?.(event.target.checked)}
                    disabled={fastModeDisabled}
                  />
                  <span className="toggle-slider" />
                </label>
              </div>
            )}
            {disabled && onStop ? (
              <button
                type="button"
                className="composer-send-fab stop"
                onClick={onStop}
                aria-label="Stop generating"
              >
                ■
              </button>
            ) : (
              <button
                type="button"
                className="composer-send-fab"
                onClick={() => void handleSend()}
                disabled={disabled || !canSend}
                aria-label="Send"
              >
                ↑
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
