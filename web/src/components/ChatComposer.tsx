import { useEffect, useRef, useState } from 'react'

type ChatComposerProps = {
  disabled?: boolean
  onSend: (message: string, files: File[]) => Promise<boolean>
  onStop?: () => void
  referenceImagesCount?: number
  examplePrompts?: string[]
  mcpTools?: string[]
  mcpToolsLoading?: boolean
  mcpToolsError?: string | null
}

const MAX_REFERENCE_IMAGES = 3

export function ChatComposer({
  disabled,
  onSend,
  onStop,
  referenceImagesCount = 0,
  examplePrompts = [],
  mcpTools = [],
  mcpToolsLoading = false,
  mcpToolsError = null
}: ChatComposerProps) {
  const [input, setInput] = useState('')
  const [dragActive, setDragActive] = useState(false)
  const [isInputFocused, setIsInputFocused] = useState(false)
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

  const handleFiles = (files: FileList | null) => {
    if (!files || files.length === 0) return
    const remaining = Math.max(0, MAX_REFERENCE_IMAGES - referenceImagesCount - pendingImages.length)
    if (remaining === 0) return
    const selection = Array.from(files).slice(0, remaining)
    setPendingImages((prev) => [
      ...prev,
      ...selection.map((file) => ({ file, previewUrl: URL.createObjectURL(file) }))
    ])
  }

  const handleDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    event.stopPropagation()
    setDragActive(false)
    if (disabled) return
    handleFiles(event.dataTransfer.files)
  }

  const handleDragOver = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    event.stopPropagation()
    if (!disabled) {
      setDragActive(true)
    }
  }

  const handleDragLeave = () => setDragActive(false)

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

  const promptOptions = examplePrompts.slice(0, 5)
  const showPromptPopover = isInputFocused && promptOptions.length > 0

  return (
    <div className="composer-shell">
      <div className="composer">
        <div
          className={`composer-upload ${dragActive ? 'active' : ''}`}
          onClick={() => {
            if (disabled) return
            fileInputRef.current?.click()
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault()
              if (!disabled) {
                fileInputRef.current?.click()
              }
            }
          }}
          onDrop={handleDrop}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          role="button"
          tabIndex={0}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            multiple
            hidden
            onChange={(event) => handleFiles(event.target.files)}
          />
          <span>
            {referenceImagesCount >= MAX_REFERENCE_IMAGES
              ? 'Reference images full'
              : 'Add images'}
          </span>
          <span className="composer-upload-sub">
            Click or drop
          </span>
        </div>
        <div className="composer-upload-meta">
          <span>{referenceImagesCount + pendingImages.length} / {MAX_REFERENCE_IMAGES}</span>
        </div>
        {pendingImages.length > 0 && (
          <div className="composer-upload-previews">
            {pendingImages.map((image, index) => (
              <div className="composer-upload-preview" key={`${image.file.name}-${index}`}>
                <img src={image.previewUrl} alt={image.file.name} />
                <button
                  className="ghost-btn"
                  type="button"
                  onClick={() => handleRemovePending(index)}
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
        )}
        <div className="composer-input-stack">
          <details className="composer-tools-panel">
            <summary className="composer-tools-summary">
              <span className="composer-tools-title">Loaded MCP tools ({mcpTools.length})</span>
              {mcpToolsLoading && (
                <span className="composer-tools-meta">Loading...</span>
              )}
              {!mcpToolsLoading && mcpToolsError && (
                <span className="composer-tools-meta error">Unavailable</span>
              )}
            </summary>
            <div className="composer-tools-body">
              {mcpTools.length > 0 ? (
                <ul className="composer-tools-list">
                  {mcpTools.map((toolName) => (
                    <li key={toolName}>{toolName}</li>
                  ))}
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
          </details>
          <div className="composer-input-area">
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
            <textarea
              ref={textareaRef}
              className="composer-input"
              placeholder="Ask the scene agent…"
              value={input}
              disabled={disabled}
              onFocus={handleInputFocus}
              onBlur={handleInputBlur}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key !== 'Enter') return
                if (event.ctrlKey || event.metaKey || event.shiftKey) {
                  return
                }
                event.preventDefault()
                void handleSend()
              }}
              rows={2}
            />
          </div>
        </div>
        <div className="composer-hint muted">Enter to send <br />Shift + Enter for newline</div>
        {disabled && onStop ? (
          <button className="primary-btn" onClick={onStop}>
            Stop
          </button>
        ) : (
          <button className="primary-btn" onClick={() => void handleSend()} disabled={disabled}>
            Send
          </button>
        )}
      </div>
    </div>
  )
}
