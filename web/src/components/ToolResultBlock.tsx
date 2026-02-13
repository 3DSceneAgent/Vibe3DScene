import { useState } from 'react'
import type { Message } from '../state/types'
import { MarkdownMessage } from './MarkdownMessage'
import { resolveMediaUrl } from '../utils/url'

type ToolResultBlockProps = {
  message: Message
  backendUrl: string
}

function formatContent(content: string): string {
  // If this looks like markdown image content, keep as-is
  if (content.includes('![') && content.includes('](')) {
    return content
  }
  // Try to parse as JSON and format compactly
  try {
    const parsed = JSON.parse(content)
    return JSON.stringify(parsed)
  } catch {
    // Not JSON, return as-is
    return content
  }
}

export function ToolResultBlock({ message, backendUrl }: ToolResultBlockProps) {
  const [collapsed, setCollapsed] = useState<boolean>(true)
  const media = message.toolMedia ?? []
  const formattedContent = formatContent(message.content || 'No tool output.')
  const hasMarkdownImage = formattedContent.includes('![') && formattedContent.includes('](')

  return (
    <div className="tool-block">
      <div className="tool-block-header">
        <span>{message.toolName ? `Tool: ${message.toolName}` : 'Tool output'}</span>
        <button className="text-btn" onClick={() => setCollapsed((prev) => !prev)}>
          {collapsed ? 'Show' : 'Hide'}
        </button>
      </div>
      {!collapsed && (
        <>
          {hasMarkdownImage ? (
            <div className="tool-block-body">
              <MarkdownMessage content={formattedContent} backendUrl={backendUrl} />
            </div>
          ) : (
            <pre className="tool-block-body">{formattedContent}</pre>
          )}
          {media.length > 0 && (
            <div className="tool-block-media">
              {media.map((item, index) => {
                const src = resolveMediaUrl(item.value, backendUrl)
                if (!src) {
                  return null
                }
                return <img key={`${message.id}-media-${index}`} src={src} alt="tool output" />
              })}
            </div>
          )}
        </>
      )}
    </div>
  )
}
