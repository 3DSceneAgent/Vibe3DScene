import { useEffect, useMemo, useRef, useState } from 'react'
import type { GraphNodeStream } from '../api/types'

type GraphTimelineProps = {
  events: GraphNodeStream[]
}

function stringifyPatchValue(value: unknown): string {
  if (value == null) return 'null'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try {
    const json = JSON.stringify(value)
    if (json.length > 180) return `${json.slice(0, 180)}...`
    return json
  } catch {
    return String(value)
  }
}

function summarizePatch(patch: Record<string, unknown> | undefined): string | null {
  if (!patch) return null
  const entries = Object.entries(patch).slice(0, 3)
  if (entries.length === 0) return null
  return entries
    .map(([key, value]) => `${key}=${stringifyPatchValue(value)}`)
    .join(' · ')
}

export function GraphTimeline({ events }: GraphTimelineProps) {
  const [collapsed, setCollapsed] = useState(false)
  const listRef = useRef<HTMLDivElement | null>(null)
  const latestEvent = useMemo(
    () => (events.length > 0 ? events[events.length - 1] : null),
    [events]
  )
  const visibleEvents = useMemo(() => (latestEvent ? [latestEvent] : []), [latestEvent])

  useEffect(() => {
    if (collapsed) return
    const list = listRef.current
    if (!list) return
    list.scrollTop = list.scrollHeight
  }, [collapsed, latestEvent])

  return (
    <section className="graph-timeline">
      <div className="graph-timeline-header">
        <div className="graph-timeline-title">Graph timeline</div>
        <div className="graph-timeline-actions">
          <span className="graph-timeline-count">{events.length} steps</span>
          <button className="text-btn" onClick={() => setCollapsed((prev) => !prev)}>
            {collapsed ? 'Show' : 'Hide'}
          </button>
        </div>
      </div>
      {!collapsed && (
        <div className="graph-timeline-list" ref={listRef}>
          {visibleEvents.length === 0 && <div className="muted">No node updates yet.</div>}
          {visibleEvents.map((event) => {
            const summary = summarizePatch(event.state_patch)
            return (
              <div className="graph-timeline-item" key={`${event.request_id}-${event.step_index}-${event.node}`}>
                <div className="graph-timeline-row">
                  <span className="graph-timeline-step">#{event.step_index}</span>
                  <span className="graph-timeline-node">{event.node}</span>
                </div>
                <div className="graph-timeline-keys">updates: {event.update_keys.join(', ') || 'none'}</div>
                {typeof event.message_count === 'number' && event.message_count > 0 && (
                  <div className="graph-timeline-meta">messages: {event.message_count}</div>
                )}
                {summary && <div className="graph-timeline-meta">{summary}</div>}
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}
