import { useEffect, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import type { SceneHierarchyNode } from '../state/types'
import { SceneObjectIcon } from './SceneObjectIcon'

type SceneContextMenuState = {
  node: SceneHierarchyNode
  left: number
  top: number
}

function resolveNodeInteractionTarget(node: SceneHierarchyNode): SceneHierarchyNode | null {
  if (node.backendObjectId) {
    return node
  }

  const matches = new Map<string, SceneHierarchyNode>()
  const visit = (candidate: SceneHierarchyNode) => {
    if (candidate.backendObjectId && !matches.has(candidate.backendObjectId)) {
      matches.set(candidate.backendObjectId, candidate)
    }
    candidate.children.forEach(visit)
  }

  node.children.forEach(visit)
  if (matches.size !== 1) {
    return null
  }
  return matches.values().next().value ?? null
}

type SceneInfoPanelProps = {
  hierarchy: SceneHierarchyNode[]
  collapsed: boolean
  onToggleCollapse: () => void
  isSceneSyncing: boolean
  selectedBackendObjectId: string | null
  deletingNodeId: string | null
  claimingForDeleteNodeId?: string | null
  canDeleteHierarchy: boolean
  deleteActionHint?: string | null
  onSelectNode: (backendObjectId: string) => void
  onDeleteNode: (node: SceneHierarchyNode) => void | Promise<void>
  onRefInChat?: (node: SceneHierarchyNode) => void
}

type SceneNodeItemProps = {
  node: SceneHierarchyNode
  depth: number
  expanded: Record<string, boolean>
  selectedBackendObjectId: string | null
  deletingNodeId: string | null
  claimingForDeleteNodeId?: string | null
  canDeleteHierarchy: boolean
  deleteActionHint?: string | null
  onToggleNode: (nodeId: string) => void
  onSelectNode: (backendObjectId: string) => void
  onDeleteNode: (node: SceneHierarchyNode) => void | Promise<void>
  onOpenContextMenu: (node: SceneHierarchyNode, clientX: number, clientY: number) => void
}

function clampMenuPosition(clientX: number, clientY: number, itemCount: number) {
  const menuWidth = 172
  const menuHeight = itemCount * 30 + 12
  const viewportPadding = 12
  const left = Math.min(
    Math.max(viewportPadding, clientX),
    Math.max(viewportPadding, window.innerWidth - menuWidth - viewportPadding)
  )
  const top = Math.min(
    Math.max(viewportPadding, clientY),
    Math.max(viewportPadding, window.innerHeight - menuHeight - viewportPadding)
  )
  return { left, top }
}

function SceneNodeItem({
  node,
  depth,
  expanded,
  selectedBackendObjectId,
  deletingNodeId,
  claimingForDeleteNodeId,
  canDeleteHierarchy,
  deleteActionHint,
  onToggleNode,
  onSelectNode,
  onDeleteNode,
  onOpenContextMenu
}: SceneNodeItemProps) {
  const hasChildren = node.children.length > 0
  const isExpanded = expanded[node.nodeId] ?? true
  const interactionTarget = resolveNodeInteractionTarget(node)
  const resolvedBackendObjectId = interactionTarget?.backendObjectId ?? null
  const isSelected = Boolean(resolvedBackendObjectId) && selectedBackendObjectId === resolvedBackendObjectId
  const isDeleting = deletingNodeId === node.nodeId
  const isClaiming = claimingForDeleteNodeId === node.nodeId
  const isBusy = isDeleting || isClaiming
  const deleteDisabled =
    !node.deletable || (deletingNodeId !== null && deletingNodeId !== node.nodeId) || isClaiming
  const deleteTitle = !node.deletable
    ? 'This object cannot be deleted because it has no backend identity.'
    : isClaiming
      ? 'Claiming runtime...'
      : !canDeleteHierarchy
        ? deleteActionHint ?? 'Click to claim runtime and delete.'
        : 'Delete object'
  const canSelect = Boolean(resolvedBackendObjectId)
  const canReference = Boolean(interactionTarget?.referencable !== false && resolvedBackendObjectId)

  const handleSelect = () => {
    if (!resolvedBackendObjectId) {
      return
    }
    onSelectNode(resolvedBackendObjectId)
  }

  const handleRowKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'Enter' && event.key !== ' ') {
      return
    }
    if (!canSelect) {
      return
    }
    event.preventDefault()
    handleSelect()
  }

  return (
    <div className="scene-tree-node">
      <div
        className={`scene-tree-row ${isSelected ? 'selected' : ''} ${isBusy ? 'deleting' : ''} ${canSelect ? '' : 'is-structural'}`}
        style={{ paddingLeft: `${8 + depth * 14}px` }}
        role="button"
        tabIndex={canSelect ? 0 : -1}
        aria-selected={isSelected}
        aria-disabled={!canSelect}
        onClick={handleSelect}
        onContextMenu={(event) => {
          event.preventDefault()
          event.stopPropagation()
          if (!canReference || !interactionTarget) {
            return
          }
          handleSelect()
          onOpenContextMenu(interactionTarget, event.clientX, event.clientY)
        }}
        onKeyDown={handleRowKeyDown}
      >
        {hasChildren ? (
          <button
            className="scene-tree-toggle"
            onClick={(event) => {
              event.stopPropagation()
              onToggleNode(node.nodeId)
            }}
            aria-label={isExpanded ? 'Collapse node' : 'Expand node'}
          >
            <svg width="8" height="8" viewBox="0 0 8 8" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              {isExpanded
                ? <polyline points="1.5 3 4 5.5 6.5 3" />
                : <polyline points="3 1.5 5.5 4 3 6.5" />}
            </svg>
          </button>
        ) : (
          <span className="scene-tree-toggle-spacer" />
        )}
        <div className="scene-tree-label">
          <SceneObjectIcon type={node.type} />
          <span className="scene-tree-name" title={node.name}>
            {node.name}
          </span>
        </div>
        <button
          className="scene-tree-delete-btn"
          type="button"
          onClick={(event) => {
            event.stopPropagation()
            void onDeleteNode(node)
          }}
          disabled={deleteDisabled || isBusy}
          aria-label={`Delete ${node.name}`}
          title={isClaiming ? 'Claiming runtime...' : isDeleting ? 'Deleting object...' : deleteTitle}
        >
          {isClaiming ? (
            <svg className="spin" width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
              <path d="M14 8a6 6 0 1 1-1.5-4" />
            </svg>
          ) : isDeleting ? '…' : 'x'}
        </button>
      </div>
      {hasChildren && isExpanded && (
        <div className="scene-tree-children">
          {node.children.map((child) => (
            <SceneNodeItem
              key={child.nodeId}
              node={child}
              depth={depth + 1}
              expanded={expanded}
              selectedBackendObjectId={selectedBackendObjectId}
              deletingNodeId={deletingNodeId}
              claimingForDeleteNodeId={claimingForDeleteNodeId}
              canDeleteHierarchy={canDeleteHierarchy}
              deleteActionHint={deleteActionHint}
              onToggleNode={onToggleNode}
              onSelectNode={onSelectNode}
              onDeleteNode={onDeleteNode}
              onOpenContextMenu={onOpenContextMenu}
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
  isSceneSyncing,
  selectedBackendObjectId,
  deletingNodeId,
  claimingForDeleteNodeId,
  canDeleteHierarchy,
  deleteActionHint = null,
  onSelectNode,
  onDeleteNode,
  onRefInChat
}: SceneInfoPanelProps) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [contextMenu, setContextMenu] = useState<SceneContextMenuState | null>(null)

  useEffect(() => {
    if (!contextMenu) {
      return
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setContextMenu(null)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => {
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [contextMenu])

  const openContextMenu = (node: SceneHierarchyNode, clientX: number, clientY: number) => {
    if (!node.backendObjectId || !node.referencable || !onRefInChat) {
      return
    }
    const position = clampMenuPosition(clientX, clientY, 1)
    setContextMenu({
      node,
      left: position.left,
      top: position.top
    })
  }

  return (
    <div className={`scene-objects-panel ${collapsed ? 'collapsed' : ''}`}>
      <div className="scene-objects-header">
        <div className="panel-title">Scene Objects</div>
      </div>
      <button
        className="ghost-btn scene-objects-toggle"
        onClick={onToggleCollapse}
        aria-label={collapsed ? 'Expand Scene Objects panel' : 'Collapse Scene Objects panel'}
        title={collapsed ? 'Expand' : 'Collapse'}
      >
        <span className={`scene-objects-toggle-icon ${collapsed ? '' : 'expanded'}`} aria-hidden="true">
          {'>'}
        </span>
      </button>
      {!collapsed && (
        <>
          {hierarchy.length === 0 && <div className="muted">No glTF hierarchy loaded yet.</div>}
          <div className="scene-object-scroll">
            <div className="scene-tree">
              {hierarchy.map((node) => (
                <SceneNodeItem
                  key={node.nodeId}
                  node={node}
                  depth={0}
                  expanded={expanded}
                  selectedBackendObjectId={selectedBackendObjectId}
                  deletingNodeId={deletingNodeId}
                  claimingForDeleteNodeId={claimingForDeleteNodeId}
                  canDeleteHierarchy={canDeleteHierarchy}
                  deleteActionHint={deleteActionHint}
                  onToggleNode={(nodeId) =>
                    setExpanded((current) => ({
                      ...current,
                      [nodeId]: !current[nodeId]
                    }))
                  }
                  onSelectNode={onSelectNode}
                  onDeleteNode={onDeleteNode}
                  onOpenContextMenu={openContextMenu}
                />
              ))}
            </div>
          </div>
          {isSceneSyncing && <div className="muted">Syncing scene metadata...</div>}
          {contextMenu && onRefInChat && (
            <>
              <div className="thread-context-menu-backdrop" onClick={() => setContextMenu(null)} aria-hidden="true" />
              <div
                className="thread-context-menu"
                role="menu"
                aria-label={`Actions for ${contextMenu.node.name}`}
                style={{ left: contextMenu.left, top: contextMenu.top }}
              >
                <button
                  type="button"
                  className="thread-context-menu-item"
                  role="menuitem"
                  onClick={() => {
                    setContextMenu(null)
                    onRefInChat(contextMenu.node)
                  }}
                >
                  Add to chat
                </button>
              </div>
            </>
          )}
        </>
      )}
    </div>
  )
}
