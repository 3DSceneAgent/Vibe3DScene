import { useMemo } from 'react'
import type { GraphNodeStream } from '../api/types'

type GraphTimelineProps = {
  events: GraphNodeStream[]
  isStreaming?: boolean
}

export function GraphTimeline({ events, isStreaming = false }: GraphTimelineProps) {
  const latestEvent = useMemo(
    () => (events.length > 0 ? events[events.length - 1] : null),
    [events]
  )

  if (!latestEvent) return null

  return (
    <div
      className={`graph-timeline-collapsed-chip ${isStreaming ? 'is-streaming' : 'is-static'}`}
    >
      <span
        className={`graph-timeline-collapsed-dot ${isStreaming ? 'is-streaming' : 'is-static'}`}
        aria-hidden="true"
      />
      <span className="graph-timeline-collapsed-node">{`graph: ${latestEvent.node}`}</span>
      {events.length > 1 && (
        <span className="graph-timeline-step-count">#{latestEvent.step_index}</span>
      )}
    </div>
  )
}
