import type { PendingImageAttachment, Thread } from '../state/types'
import { MessageList } from './MessageList'
import { ChatComposer } from './ChatComposer'
import { GraphTimeline } from './GraphTimeline'
import { TodoPanel } from './TodoPanel'
import type { GraphNodeStream, TodoItem, VlmProviderOption } from '../api/types'

type ChatTabProps = {
  thread: Thread | null
  isStreaming: boolean
  streamStatus: 'streaming' | 'complete'
  onSend: (message: string, images: PendingImageAttachment[]) => Promise<boolean>
  onRetryTurn?: (turnId: string) => void
  onStop?: () => void
  backendUrl: string
  examplePrompts: string[]
  promptHistory?: string[]
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
  minimalUi?: boolean
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

export function ChatTab({
  thread,
  isStreaming,
  streamStatus,
  onSend,
  onRetryTurn,
  onStop,
  backendUrl,
  examplePrompts,
  promptHistory = [],
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
  runtimeClaimHint = null,
  minimalUi = false
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
    <div className={`chat-tab chat-pane ${minimalUi ? 'minimal-ui' : ''}`}>
      {!minimalUi && (
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
          <GraphTimeline events={graphEvents} isStreaming={streamStatus === 'streaming'} />
        </div>
      )}
      {!minimalUi && <TodoPanel todos={todos} activeTodoId={activeTodoId} fastMode={fastMode} isStreaming={streamStatus === 'streaming'} />}
      <div className="chat-scroll-area">
        <MessageList
          messages={thread.messages}
          backendUrl={backendUrl}
          streamStatus={streamStatus}
          onRetryTurn={onRetryTurn}
        />
      </div>
      <ChatComposer
        disabled={isStreaming}
        onSend={onSend}
        onStop={onStop}
        referenceImagesCount={thread.images?.length ?? 0}
        examplePrompts={availablePrompts}
        promptHistory={promptHistory}
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
        showModelSelector={!minimalUi}
        showMcpTools={!minimalUi}
        showFastMode={!minimalUi}
        runtimeHint={!minimalUi ? runtimeClaimHint : null}
      />
    </div>
  )
}
