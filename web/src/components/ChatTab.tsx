import type { PendingImageAttachment, Thread } from '../state/types'
import { MessageList } from './MessageList'
import { ChatComposer } from './ChatComposer'
import { GraphTimeline } from './GraphTimeline'
import { TelemetrySummary } from './TelemetrySummary'
import { TodoPanel } from './TodoPanel'
import type { GraphNodeStream, TelemetryMetrics, TodoItem, VlmProviderOption } from '../api/types'

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

function toTelemetryFromProgress(thread: Thread | null): TelemetryMetrics | null {
  const progress = thread?.streamSession?.progress
  if (!progress) return null
  return {
    input_tokens: progress.llm_input_tokens,
    output_tokens: progress.llm_output_tokens,
    total_tokens: progress.llm_total_tokens,
    image_input_tokens: progress.image_input_tokens,
    has_image_inputs:
      typeof progress.image_input_tokens === 'number' || progress.image_input_tokens === null,
    tool_call_count: progress.tool_calls_started ?? 0,
    peak_context_used_tokens: progress.peak_context_used_tokens,
    peak_context_limit_tokens: progress.peak_context_limit_tokens
  }
}

const THREAD_TELEMETRY_HELP_LINES = [
  'Tokens shows the current saved total for this thread.',
  'Live only counts the run still streaming; retry rewinds the old attempt first.',
  'In adds up every internal model call in the thread, including router, builder, verifier, and other helpers.',
  'Ctx is only the largest single model call, so it is often smaller than In.',
  'Image tokens are included in In; renders are usually sent after being capped to 960x540.'
]

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

  const liveTelemetry = toTelemetryFromProgress(thread)

  return (
    <div className={`chat-tab chat-pane ${minimalUi ? 'minimal-ui' : ''}`}>
      {!minimalUi && (
        <div className="chat-status-stack">
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
          {thread.threadMetrics || (streamStatus === 'streaming' && liveTelemetry) ? (
            <div className="chat-status-meta">
              <TelemetrySummary
                label="Tokens"
                metrics={thread.threadMetrics}
                variant="inline"
                helpLines={THREAD_TELEMETRY_HELP_LINES}
              />
              {streamStatus === 'streaming' ? (
                <TelemetrySummary
                  label="Live"
                  metrics={liveTelemetry}
                  variant="inline"
                  compact
                />
              ) : null}
            </div>
          ) : null}
        </div>
      )}
      <div className="chat-scroll-area">
        <MessageList
          messages={thread.messages}
          backendUrl={backendUrl}
          streamStatus={streamStatus}
          onRetryTurn={onRetryTurn}
        />
      </div>
      <div className="chat-composer-dock">
        {!minimalUi && (
          <TodoPanel
            todos={todos}
            activeTodoId={activeTodoId}
            fastMode={fastMode}
            isStreaming={streamStatus === 'streaming'}
          />
        )}
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
    </div>
  )
}
