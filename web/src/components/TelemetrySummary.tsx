import { useEffect, useRef, useState } from 'react'

import type { TelemetryMetrics } from '../api/types'

type TelemetrySummaryProps = {
  metrics?: TelemetryMetrics | null
  label?: string
  compact?: boolean
  variant?: 'chips' | 'inline'
  helpLines?: string[]
}

function formatMetric(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '-'
  }
  return new Intl.NumberFormat('en-US').format(value)
}

function formatTokensShort(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '-'
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`
  return String(value)
}

function hasVisibleMetrics(metrics?: TelemetryMetrics | null): boolean {
  if (!metrics) return false
  const numericValues = [
    metrics.input_tokens,
    metrics.output_tokens,
    metrics.total_tokens,
    metrics.image_input_tokens,
    metrics.peak_context_used_tokens,
    metrics.peak_context_limit_tokens
  ]
  const hasPositiveNumber = numericValues.some(
    (value) => typeof value === 'number' && Number.isFinite(value) && value > 0
  )
  const hasUnknownValue = numericValues.some((value) => value === null)
  return (
    hasPositiveNumber ||
    hasUnknownValue ||
    (typeof metrics.tool_call_count === 'number' && metrics.tool_call_count > 0) ||
    (typeof metrics.llm_call_count === 'number' && metrics.llm_call_count > 0)
  )
}

export function TelemetrySummary({
  metrics,
  label,
  compact = false,
  variant = 'chips',
  helpLines = []
}: TelemetrySummaryProps) {
  const [helpOpen, setHelpOpen] = useState(false)
  const helpRef = useRef<HTMLSpanElement | null>(null)

  useEffect(() => {
    if (!helpOpen) return undefined

    const handlePointerDown = (event: MouseEvent) => {
      const node = helpRef.current
      if (!node) return
      if (event.target instanceof Node && node.contains(event.target)) {
        return
      }
      setHelpOpen(false)
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setHelpOpen(false)
      }
    }

    document.addEventListener('mousedown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('mousedown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [helpOpen])

  if (!hasVisibleMetrics(metrics)) {
    return null
  }

  const toolCalls = typeof metrics?.tool_call_count === 'number' ? metrics.tool_call_count : 0

  if (variant === 'inline' && compact) {
    const parts: string[] = []
    const safeNum = (v: number | null | undefined): number =>
      typeof v === 'number' && Number.isFinite(v) && v > 0 ? v : 0
    const input = safeNum(metrics?.input_tokens)
    const output = safeNum(metrics?.output_tokens)
    const total = safeNum(metrics?.total_tokens) || (input + output) || 0
    if (total > 0) {
      parts.push(`${formatTokensShort(total)} tokens`)
    }
    if (input > 0 || output > 0) {
      parts.push(`in ${formatTokensShort(input)} / out ${formatTokensShort(output)}`)
    }
    if (toolCalls > 0) {
      parts.push(`${toolCalls} tool${toolCalls !== 1 ? 's' : ''}`)
    }
    if (parts.length === 0) return null
    return (
      <span className="telemetry-compact-inline">
        {label ? <span className="telemetry-compact-label">{label}</span> : null}
        <span className="telemetry-compact-body">{parts.join(' · ')}</span>
      </span>
    )
  }

  const showImageMetric = metrics?.has_image_inputs === true
  const items = [
    { key: 'input', label: 'In', value: formatMetric(metrics?.input_tokens) },
    { key: 'output', label: 'Out', value: formatMetric(metrics?.output_tokens) },
    { key: 'total', label: 'Total', value: formatMetric(metrics?.total_tokens) },
    ...(showImageMetric
      ? [{ key: 'image', label: 'Img', value: formatMetric(metrics?.image_input_tokens) }]
      : []),
    { key: 'tools', label: 'Tools', value: formatMetric(toolCalls) },
    {
      key: 'context',
      label: 'Ctx',
      value: `${formatMetric(metrics?.peak_context_used_tokens)} / ${formatMetric(metrics?.peak_context_limit_tokens)}`
    }
  ]

  if (variant === 'inline') {
    return (
      <div className="telemetry-summary telemetry-summary-inline">
        {label || helpLines.length > 0 ? (
          <span className="telemetry-inline-heading">
            {label ? <span className="telemetry-inline-label">{label}</span> : null}
            {helpLines.length > 0 ? (
              <span ref={helpRef} className="telemetry-help">
                <button
                  type="button"
                  className="telemetry-help-summary"
                  aria-label={`About ${label ?? 'telemetry'}`}
                  aria-expanded={helpOpen}
                  onClick={() => setHelpOpen((current) => !current)}
                >
                  ?
                </button>
                {helpOpen ? (
                  <div className="telemetry-help-popover" role="note">
                    {helpLines.map((line) => (
                      <p key={line}>{line}</p>
                    ))}
                  </div>
                ) : null}
              </span>
            ) : null}
          </span>
        ) : null}
        {items.map((item) => (
          <span key={item.key} className="telemetry-inline-item">
            <span className="telemetry-inline-key">{item.label}</span>
            <span className="telemetry-inline-value">{item.value}</span>
          </span>
        ))}
      </div>
    )
  }

  return (
    <div className={`telemetry-summary ${compact ? 'compact' : ''}`}>
      {label ? <span className="telemetry-summary-label">{label}</span> : null}
      {items.map((item) => (
        <span key={item.key} className="telemetry-chip">
          {item.label} {item.value}
        </span>
      ))}
    </div>
  )
}
