import { useState } from 'react'
import type { Message } from '../state/types'
import { MarkdownMessage } from './MarkdownMessage'

type ToolResultBlockProps = {
  message: Message
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

export function ToolResultBlock({ message }: ToolResultBlockProps) {
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
              <MarkdownMessage content={formattedContent} />
            </div>
          ) : (
            <pre className="tool-block-body">{formattedContent}</pre>
          )}
          {media.length > 0 && (
            <div className="tool-block-media">
              {media.map((item, index) => (
                <img key={`${message.id}-media-${index}`} src={item.value} alt="tool output" />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}
