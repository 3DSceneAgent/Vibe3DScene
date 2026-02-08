import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Components } from 'react-markdown'

type MarkdownMessageProps = {
  content: string
}

export function MarkdownMessage({ content }: MarkdownMessageProps) {
  const components: Components = {
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
    },
    img({ src, alt }) {
      return (
        <img
          src={src}
          alt={alt || 'image'}
          className="markdown-image"
          loading="lazy"
        />
      )
    }
  }

  return (
    <div className="markdown-body">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content || ''}
      </ReactMarkdown>
    </div>
  )
}
