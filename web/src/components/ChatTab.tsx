import type { Thread } from '../state/types'
import { MessageList } from './MessageList'
import { ChatComposer } from './ChatComposer'
import { ReferenceImageStrip } from './ReferenceImageStrip'
import { GraphTimeline } from './GraphTimeline'
import { TodoPanel } from './TodoPanel'
import type { GraphNodeStream, ImageAsset, TodoItem, VlmProviderOption } from '../api/types'

type ChatTabProps = {
  thread: Thread | null
  isStreaming: boolean
  streamStatus: 'streaming' | 'complete'
  onSend: (message: string, files: File[]) => Promise<boolean>
  onStop?: () => void
  backendUrl: string
  examplePrompts: string[]
  mcpTools: string[]
  mcpToolHints?: Record<string, string>
  mcpToolEnabled?: Record<string, boolean>
  onMcpToolToggle?: (toolName: string, enabled: boolean) => void
  mcpToolsLoading?: boolean
  mcpToolsError?: string | null
  vlmProviders?: VlmProviderOption[]
  vlmProvider?: string
  vlmModel?: string
  fastMode?: boolean
  fastModeAvailable?: boolean
  vlmLoading?: boolean
  vlmError?: string | null
  vlmLocked?: boolean
  onVlmSelectionChange?: (provider: string, model: string) => void
  onFastModeToggle?: (enabled: boolean) => void
  graphEvents?: GraphNodeStream[]
  todos?: TodoItem[]
  runtimeClaimHint?: string | null
}

type VlmSelectionOption = {
  value: string
  label: string
  provider: string
  model: string
}

function toSelectionValue(provider: string, model: string): string {
  return `${encodeURIComponent(provider)}::${encodeURIComponent(model)}`
}

function normalizeWorkflowValue(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const normalized = value.trim()
  return normalized || null
}

function normalizeBooleanValue(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null
}

export function ChatTab({
  thread,
  isStreaming,
  streamStatus,
  onSend,
  onStop,
  backendUrl,
  examplePrompts,
  mcpTools,
  mcpToolHints,
  mcpToolEnabled,
  onMcpToolToggle,
  mcpToolsLoading,
  mcpToolsError,
  vlmProviders = [],
  vlmProvider,
  vlmModel,
  fastMode = false,
  fastModeAvailable = false,
  vlmLoading = false,
  vlmError = null,
  vlmLocked = false,
  onVlmSelectionChange,
  onFastModeToggle,
  graphEvents = [],
  todos = [],
  runtimeClaimHint = null
}: ChatTabProps) {
  const selectionOptions: VlmSelectionOption[] = vlmProviders
    .filter((provider) => provider.configured)
    .flatMap((provider) => {
      const models = provider.models.length > 0 ? provider.models : [provider.default_model]
      return models.map((model) => ({
        value: toSelectionValue(provider.provider, model),
        label: `${provider.display_name} / ${model}`,
        provider: provider.provider,
        model
      }))
    })

  const selectedOption =
    selectionOptions.find(
      (option) => option.provider === vlmProvider && option.model === vlmModel
    ) ?? selectionOptions[0] ?? null
  const selectorDisabled = vlmLocked
  const availablePrompts = thread?.messages.length === 0 ? examplePrompts : []
  const workflowBadgeLabel = (() => {
    let taskMode: string | null = null
    let topology: string | null = null
    let resolvedFastMode: boolean | null = null

    for (let index = graphEvents.length - 1; index >= 0; index -= 1) {
      const patchRaw = graphEvents[index]?.state_patch
      if (!patchRaw || typeof patchRaw !== 'object' || Array.isArray(patchRaw)) {
        continue
      }
      const patch = patchRaw as Record<string, unknown>
      if (!taskMode) {
        taskMode = normalizeWorkflowValue(patch.task_mode)
      }
      if (!topology) {
        topology = normalizeWorkflowValue(patch.workflow_topology)
      }
      if (resolvedFastMode === null) {
        resolvedFastMode = normalizeBooleanValue(patch.fast_mode)
      }
      if (taskMode && topology && resolvedFastMode !== null) {
        break
      }
    }

    const effectiveFastMode = resolvedFastMode ?? Boolean(fastMode)
    if (!taskMode && !topology) {
      return {
        workflow: 'Workflow: pending',
        fastMode: `Fast mode: ${effectiveFastMode ? 'on' : 'off'}`
      }
    }
    return {
      workflow: `Workflow: ${taskMode ?? 'unknown'} / ${topology ?? 'unknown'}`,
      fastMode: `Fast mode: ${effectiveFastMode ? 'on' : 'off'}`
    }
  })()
  const activeTodoId = (() => {
    for (let index = graphEvents.length - 1; index >= 0; index -= 1) {
      const patchRaw = graphEvents[index]?.state_patch
      if (!patchRaw || typeof patchRaw !== 'object' || Array.isArray(patchRaw)) {
        continue
      }
      const patch = patchRaw as Record<string, unknown>
      const candidate = normalizeWorkflowValue(patch.active_todo_id)
      if (candidate) {
        return candidate
      }
    }
    return null
  })()

  if (!thread) {
    return <div className="empty-state">Create a conversation to begin.</div>
  }

  return (
    <div className="chat-tab chat-pane">
      <div className="chat-status-row">
        <div
          className={`chat-stream-status ${streamStatus === 'streaming' ? 'streaming' : 'complete'}`}
          role="status"
          aria-live="polite"
        >
          <span className="chat-stream-status-dot" />
          <span className="chat-stream-status-text">
            {streamStatus === 'streaming' ? 'Agent is building the scene' : 'Agent ready'}
          </span>
        </div>
        <div className="chat-badge-group" aria-live="polite">
          <div className="chat-workflow-badge">{workflowBadgeLabel.workflow}</div>
          {(fastModeAvailable || Boolean(fastMode)) && (
            <div className="chat-workflow-badge chat-fastmode-badge">
              {workflowBadgeLabel.fastMode}
            </div>
          )}
        </div>
      </div>
      <GraphTimeline events={graphEvents} isStreaming={streamStatus === 'streaming'} />
      <TodoPanel todos={todos} activeTodoId={activeTodoId} />
      <div className="chat-scroll-area">
        <MessageList messages={thread.messages} backendUrl={backendUrl} />
      </div>
      {thread.images && thread.images.length > 0 && (
        <ReferenceImageStrip images={thread.images as ImageAsset[]} />
      )}
      {runtimeClaimHint && <div className="chat-runtime-hint">{runtimeClaimHint}</div>}
      <ChatComposer
        disabled={isStreaming}
        onSend={onSend}
        onStop={onStop}
        referenceImagesCount={thread.images?.length ?? 0}
        examplePrompts={availablePrompts}
        mcpTools={mcpTools}
        mcpToolHints={mcpToolHints}
        mcpToolEnabled={mcpToolEnabled}
        onMcpToolToggle={onMcpToolToggle}
        mcpToolsLoading={mcpToolsLoading}
        mcpToolsError={mcpToolsError}
        modelOptions={selectionOptions.map((option) => ({
          value: option.value,
          label: option.label
        }))}
        selectedModelValue={selectedOption?.value ?? ''}
        onModelSelectionChange={(value) => {
          const option = selectionOptions.find((entry) => entry.value === value)
          if (!option) return
          onVlmSelectionChange?.(option.provider, option.model)
        }}
        fastMode={fastMode}
        fastModeAvailable={fastModeAvailable}
        onFastModeToggle={onFastModeToggle}
        modelLoading={vlmLoading}
        modelError={vlmError}
        modelLocked={selectorDisabled}
      />
    </div>
  )
}
