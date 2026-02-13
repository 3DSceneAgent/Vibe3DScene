import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Components } from 'react-markdown'
import { resolveMediaUrl } from '../utils/url'

type MarkdownMessageProps = {
  content: string
  backendUrl: string
}

export function MarkdownMessage({ content, backendUrl }: MarkdownMessageProps) {
  const components: Components = {
    code({ children, className, ...props }) {
      const isInline = typeof className !== 'string' || !className.includes('language-')
      if (isInline) {
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
      const resolvedSrc = resolveMediaUrl(src, backendUrl)
      if (!resolvedSrc) {
        return null
      }
      return (
        <img
          src={resolvedSrc}
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
