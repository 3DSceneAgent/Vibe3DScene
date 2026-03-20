import { useState } from 'react'
import type { Message } from '../state/types'
import { MarkdownMessage } from './MarkdownMessage'
import { resolveMediaUrl } from '../utils/url'

type ToolResultBlockProps = {
  message: Message
  backendUrl: string
}

function formatContent(content: string): string {
  if (content.includes('![') && content.includes('](')) {
    return content
  }
  try {
    const parsed = JSON.parse(content)
    return JSON.stringify(parsed)
  } catch {
    return content
  }
}

export function ToolResultBlock({ message, backendUrl }: ToolResultBlockProps) {
  const [collapsed, setCollapsed] = useState<boolean>(true)
  const media = message.toolMedia ?? []
  const isStreaming = message.status === 'streaming'
  const formattedContent = formatContent(message.content || 'No tool output.')
  const hasMarkdownImage = formattedContent.includes('![') && formattedContent.includes('](')

  if (isStreaming) {
    return (
      <div className="tool-chip is-streaming">
        <div className="tool-chip-toggle tool-chip-toggle-static sweep-active">
          <span className="tool-chip-icon" aria-hidden="true">&#9881;</span>
          <span className="tool-chip-name">{message.toolName || 'Running tool'}</span>
          <span className="tool-chip-action">Running</span>
        </div>
      </div>
    )
  }

  return (
    <div className="tool-chip">
      <button className="tool-chip-toggle" onClick={() => setCollapsed((prev) => !prev)}>
        <span className="tool-chip-icon" aria-hidden="true">&#9881;</span>
        <span className="tool-chip-name">{message.toolName || 'Tool output'}</span>
        <span className="tool-chip-action">{collapsed ? 'Show' : 'Hide'}</span>
      </button>
      {!collapsed && (
        <div className="tool-chip-body">
          {hasMarkdownImage ? (
            <div className="tool-chip-content">
              <MarkdownMessage content={formattedContent} backendUrl={backendUrl} />
            </div>
          ) : (
            <pre className="tool-chip-content">{formattedContent}</pre>
          )}
          {!hasMarkdownImage && media.length > 0 && (
            <div className="tool-chip-media">
              {media.map((item, index) => {
                const src = resolveMediaUrl(item.value, backendUrl)
                if (!src) return null
                return <img key={`${message.id}-media-${index}`} src={src} alt="tool output" />
              })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
