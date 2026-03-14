import { useEffect, useRef, useState } from 'react'

type ModelOption = {
  value: string
  label: string
}

type ChatComposerProps = {
  disabled?: boolean
  onSend: (message: string, files: File[]) => Promise<boolean>
  onStop?: () => void
  referenceImagesCount?: number
  examplePrompts?: string[]
  mcpTools?: string[]
  mcpToolHints?: Record<string, string>
  mcpToolEnabled?: Record<string, boolean>
  onMcpToolToggle?: (toolName: string, enabled: boolean) => void
  mcpToolsLoading?: boolean
  mcpToolsError?: string | null
  modelOptions?: ModelOption[]
  selectedModelValue?: string
  onModelSelectionChange?: (value: string) => void
  fastMode?: boolean
  onFastModeToggle?: (enabled: boolean) => void
  fastModeAvailable?: boolean
  modelLoading?: boolean
  modelError?: string | null
  modelLocked?: boolean
}

const MAX_REFERENCE_IMAGES = 3
const MAX_EXAMPLE_PROMPTS = 10

export function ChatComposer({
  disabled,
  onSend,
  onStop,
  referenceImagesCount = 0,
  examplePrompts = [],
  mcpTools = [],
  mcpToolHints = {},
  mcpToolEnabled = {},
  onMcpToolToggle,
  mcpToolsLoading = false,
  mcpToolsError = null,
  modelOptions = [],
  selectedModelValue = '',
  onModelSelectionChange,
  fastMode = false,
  onFastModeToggle,
  fastModeAvailable = false,
  modelLoading = false,
  modelError = null,
  modelLocked = false
}: ChatComposerProps) {
  const [input, setInput] = useState('')
  const [dragActive, setDragActive] = useState(false)
  const [isInputFocused, setIsInputFocused] = useState(false)
  const [isToolsOpen, setIsToolsOpen] = useState(false)
  const [pendingImages, setPendingImages] = useState<Array<{ file: File; previewUrl: string }>>([])
  const blurTimeoutRef = useRef<number | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  useEffect(() => {
    return () => {
      pendingImages.forEach((image) => URL.revokeObjectURL(image.previewUrl))
      if (blurTimeoutRef.current !== null) {
        window.clearTimeout(blurTimeoutRef.current)
      }
    }
  }, [pendingImages])

  const handleSend = async () => {
    const text = input.trim()
    if (!text) return
    const success = await onSend(text, pendingImages.map((image) => image.file))
    if (success) {
      setInput('')
      pendingImages.forEach((image) => URL.revokeObjectURL(image.previewUrl))
      setPendingImages([])
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }
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
    textareaRef.current?.focus()
  }

  const handleInputFocus = () => {
    if (blurTimeoutRef.current !== null) {
      window.clearTimeout(blurTimeoutRef.current)
      blurTimeoutRef.current = null
    }
    setIsInputFocused(true)
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

  const promptOptions = examplePrompts.slice(0, MAX_EXAMPLE_PROMPTS)
  const showPromptPopover = isInputFocused && input.trim().length === 0 && promptOptions.length > 0
  const modelSelectDisabled = modelLocked || modelLoading || modelOptions.length === 0
  const hasPendingImages = pendingImages.length > 0
  const canSend = input.trim().length > 0
  const enabledToolCount = mcpTools.filter((toolName) => mcpToolEnabled[toolName] !== false).length
  const fastModeDisabled = Boolean(disabled)

  const shortenFilename = (filename: string) => {
    const stem = filename.replace(/\.[^/.]+$/, '')
    return stem.slice(0, 10)
  }

  return (
    <div className="composer-shell">
      <div className={`composer-top-row ${fastModeAvailable ? 'has-fast-mode' : ''}`}>
        <div className="composer-model-panel">
          <select
            id="composer-model-select"
            className="composer-model-select"
            value={selectedModelValue}
            disabled={modelSelectDisabled}
            onChange={(event) => onModelSelectionChange?.(event.target.value)}
          >
            {modelOptions.length === 0 && <option value="">No model available</option>}
            {modelOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
        <div
          className={`composer-tools-panel ${isToolsOpen ? 'open' : ''}`}
          onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
              setIsToolsOpen(false)
            }
          }}
        >
          <button
            type="button"
            className="composer-tools-summary"
            onClick={() => setIsToolsOpen((prev) => !prev)}
            aria-expanded={isToolsOpen}
          >
            <span className="composer-tools-title">MCP tools ({enabledToolCount}/{mcpTools.length})</span>
            {mcpToolsLoading && <span className="composer-tools-meta">Loading...</span>}
            {!mcpToolsLoading && mcpToolsError && (
              <span className="composer-tools-meta error">Unavailable</span>
            )}
          </button>
          {isToolsOpen && (
            <div className="composer-tools-body">
              {mcpTools.length > 0 ? (
                <ul className="composer-tools-list">
                  {mcpTools.map((toolName) => {
                    const hint = mcpToolHints[toolName] || `MCP tool: ${toolName}`
                    return (
                      <li key={toolName} className="composer-tools-item">
                        <label className="composer-tools-toggle" title={hint}>
                        <input
                          type="checkbox"
                          checked={mcpToolEnabled[toolName] !== false}
                          onChange={(event) => onMcpToolToggle?.(toolName, event.target.checked)}
                          disabled={disabled || mcpToolsLoading}
                        />
                          <span className="composer-tools-name">{toolName}</span>
                        </label>
                      </li>
                    )
                  })}
                </ul>
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
        {fastModeAvailable && (
          <div className={`composer-fast-mode-panel ${fastModeDisabled ? 'is-disabled' : ''}`}>
            <div className="composer-fast-mode-copy">
              <div className="composer-fast-mode-title">Fast mode</div>
              <div className="composer-fast-mode-meta">
                Skip auto observe and verify. Faster, lower fidelity.
              </div>
            </div>
            <label className="toggle-switch" aria-label="Toggle fast mode">
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
      </div>
      {(modelLoading || modelLocked || modelError) && (
        <div className="composer-model-meta muted">
          {modelLoading
            ? 'Loading model options...'
            : modelLocked
              ? 'Model switch is currently locked'
              : modelError}
        </div>
      )}
      <div
        className={`composer-input-panel ${dragActive ? 'drag-active' : ''} ${hasPendingImages ? 'has-images' : ''}`}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
      >
        {showPromptPopover && (
          <div className="composer-prompt-popover" role="listbox" aria-label="Example prompts">
            {promptOptions.map((prompt, index) => (
              <button
                key={`${index}-${prompt}`}
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
        <textarea
          ref={textareaRef}
          className="composer-input"
          placeholder="Ask the scene agent..."
          value={input}
          disabled={disabled}
          onFocus={handleInputFocus}
          onBlur={handleInputBlur}
          onPaste={handlePaste}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
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
        <button
          type="button"
          className="composer-plus-btn"
          onClick={() => {
            if (disabled) return
            fileInputRef.current?.click()
          }}
          disabled={disabled || referenceImagesCount + pendingImages.length >= MAX_REFERENCE_IMAGES}
          aria-label="Add images"
        >
          +
        </button>
        {disabled && onStop ? (
          <button className="composer-send-fab stop" onClick={onStop} aria-label="Stop generating">
            ■
          </button>
        ) : (
          <button
            className="composer-send-fab"
            onClick={() => void handleSend()}
            disabled={disabled || !canSend}
            aria-label="Send"
          >
            ↑
          </button>
        )}
        <div className="composer-attachment-count">
          {referenceImagesCount + pendingImages.length} / {MAX_REFERENCE_IMAGES} images
        </div>
      </div>
    </div>
  )
}
