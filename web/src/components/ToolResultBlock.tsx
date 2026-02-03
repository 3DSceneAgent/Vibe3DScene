import { useState } from 'react'
import type { Message } from '../state/types'

type ToolResultBlockProps = {
  message: Message
}

export function ToolResultBlock({ message }: ToolResultBlockProps) {
  const [collapsed, setCollapsed] = useState<boolean>(Boolean(message.collapsed))
  const media = message.toolMedia ?? []

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
          <pre className="tool-block-body">{message.content || 'No tool output.'}</pre>
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
