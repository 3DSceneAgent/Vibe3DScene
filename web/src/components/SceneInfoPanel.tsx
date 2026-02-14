import { useState } from 'react'
import type { SceneHierarchyNode } from '../state/types'

type SceneInfoPanelProps = {
  hierarchy: SceneHierarchyNode[]
  collapsed: boolean
  onToggleCollapse: () => void
  isSceneSyncing: boolean
}

type SceneNodeItemProps = {
  node: SceneHierarchyNode
  depth: number
  expanded: Record<string, boolean>
  onToggleNode: (id: string) => void
}

function SceneNodeItem({ node, depth, expanded, onToggleNode }: SceneNodeItemProps) {
  const hasChildren = node.children.length > 0
  const isExpanded = expanded[node.id] ?? true

  return (
    <div className="scene-tree-node">
      <div className="scene-tree-row" style={{ paddingLeft: `${8 + depth * 14}px` }}>
        <button
          className={`scene-tree-toggle ${hasChildren ? '' : 'empty'}`}
          onClick={() => {
            if (hasChildren) {
              onToggleNode(node.id)
            }
          }}
          aria-label={hasChildren ? (isExpanded ? 'Collapse node' : 'Expand node') : 'Leaf node'}
          disabled={!hasChildren}
        >
          {hasChildren ? (isExpanded ? 'v' : '>') : '.'}
        </button>
        <div className="scene-tree-label">
          <span className="scene-tree-name">{node.name}</span>
          <span className="scene-tree-type">{node.type}</span>
        </div>
      </div>
      {hasChildren && isExpanded && (
        <div className="scene-tree-children">
          {node.children.map((child) => (
            <SceneNodeItem
              key={child.id}
              node={child}
              depth={depth + 1}
              expanded={expanded}
              onToggleNode={onToggleNode}
            />
          ))}
        </div>
      )}
    </div>
  )
}

export function SceneInfoPanel({
  hierarchy,
  collapsed,
  onToggleCollapse,
  isSceneSyncing
}: SceneInfoPanelProps) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})

  return (
    <div className={`scene-objects-panel ${collapsed ? 'collapsed' : ''}`}>
      <div className="scene-objects-header">
        <div className="panel-title">Scene Objects</div>
        <button className="ghost-btn scene-objects-toggle" onClick={onToggleCollapse}>
          {collapsed ? 'Show' : 'Hide'}
        </button>
      </div>
      {!collapsed && (
        <>
          {hierarchy.length === 0 && <div className="muted">No glTF hierarchy loaded yet.</div>}
          <div className="scene-object-scroll">
            <div className="scene-tree">
              {hierarchy.map((node) => (
                <SceneNodeItem
                  key={node.id}
                  node={node}
                  depth={0}
                  expanded={expanded}
                  onToggleNode={(id) =>
                    setExpanded((current) => ({
                      ...current,
                      [id]: !current[id]
                    }))
                  }
                />
              ))}
            </div>
          </div>
          {isSceneSyncing && <div className="muted">Syncing scene metadata...</div>}
        </>
      )}
    </div>
  )
}
