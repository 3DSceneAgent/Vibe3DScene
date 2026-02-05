import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

type MarkdownMessageProps = {
  content: string
}

export function MarkdownMessage({ content }: MarkdownMessageProps) {
  return (
    <div className="markdown-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code({ inline, children, ...props }) {
            if (inline) {
              return (
                <code className="markdown-inline-code" {...props}>
                  {children}
                </code>
              )
            }
            return (
              <pre className="markdown-code-block">
                <code {...props}>{children}</code>
              </pre>
            )
          },
          a({ children, ...props }) {
            return (
              <a {...props} rel="noreferrer noopener" target="_blank">
                {children}
              </a>
            )
          }
        }}
      >
        {content || ''}
      </ReactMarkdown>
    </div>
  )
}
