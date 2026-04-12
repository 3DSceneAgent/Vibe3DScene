import type {
  PendingImageAttachment,
  SceneHierarchyNode,
  SceneObjectReference,
  SceneObjectReferenceInsertion,
  Thread
} from '../state/types'
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
  onSend: (
    message: string,
    images: PendingImageAttachment[],
    referencedObjects: SceneObjectReference[]
  ) => Promise<boolean>
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
  providerThinking?: boolean | null
  fastMode?: boolean
  fastModeAvailable?: boolean
  vlmLoading?: boolean
  vlmError?: string | null
  vlmLocked?: boolean
  onVlmSelectionChange?: (provider: string, model: string) => void
  onProviderThinkingToggle?: (enabled: boolean) => void
  onFastModeToggle?: (enabled: boolean) => void
  onBackToConsole?: () => void
  graphEvents?: GraphNodeStream[]
  todos?: TodoItem[]
  runtimeClaimHint?: string | null
  occupyingResources?: boolean
  claimingRuntime?: boolean
  onClaimRuntime?: () => void
  onReleaseRuntime?: () => void
  minimalUi?: boolean
  pendingReferenceInsertion?: SceneObjectReferenceInsertion | null
  onPendingReferenceInsertionHandled?: (key: string) => void
  agentTurnLimitNotice?: { limit: number | null } | null
  onContinueAfterAgentTurnLimit?: () => void
}

type VlmSelectionOption = {
  value: string
  label: string
  provider: string
  model: string
}

function collectReferencedSceneObjects(nodes: SceneHierarchyNode[]): SceneObjectReference[] {
  const collected: SceneObjectReference[] = []
  const seen = new Set<string>()

  const visit = (entries: SceneHierarchyNode[]) => {
    entries.forEach((node) => {
      if (node.backendObjectId && node.referencable && !seen.has(node.backendObjectId)) {
        seen.add(node.backendObjectId)
        collected.push({
          backendObjectId: node.backendObjectId,
          displayName: node.backendObjectName ?? node.name,
          objectType: node.type
        })
      }
      if (node.children.length > 0) {
        visit(node.children)
      }
    })
  }

  visit(nodes)
  return collected
}

function toSelectionValue(provider: string, model: string): string {
  return `${encodeURIComponent(provider)}::${encodeURIComponent(model)}`
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
    llm_call_count: progress.llm_call_count,
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
  providerThinking = null,
  fastMode = false,
  fastModeAvailable = false,
  vlmLoading = false,
  vlmError = null,
  vlmLocked = false,
  onVlmSelectionChange,
  onProviderThinkingToggle,
  onFastModeToggle,
  onBackToConsole,
  graphEvents = [],
  todos = [],
  runtimeClaimHint = null,
  occupyingResources = false,
  claimingRuntime = false,
  onClaimRuntime,
  onReleaseRuntime,
  minimalUi = false,
  pendingReferenceInsertion = null,
  onPendingReferenceInsertionHandled,
  agentTurnLimitNotice = null,
  onContinueAfterAgentTurnLimit
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
  const providerThinkingAvailable = vlmProvider === 'gemini' || vlmProvider === 'qwen'
  const availablePrompts = thread?.messages.length === 0 ? examplePrompts : []
  const activeTodoId = thread?.activeTodoId ?? null

  if (!thread) {
    return <div className="empty-state">Create a conversation to begin.</div>
  }

  const liveTelemetry = toTelemetryFromProgress(thread)
  const availableSceneObjectReferences = collectReferencedSceneObjects(thread.sceneHierarchy ?? [])

  return (
    <div className={`chat-tab chat-pane ${minimalUi ? 'minimal-ui' : ''}`}>
      {!minimalUi && (
        <div className="chat-status-stack">
          <div className="chat-status-row">
            {onBackToConsole && (
              <button
                type="button"
                className="ghost-btn chat-home-btn"
                onClick={onBackToConsole}
                title="Back to console"
                aria-label="Back to console"
              >
                <svg viewBox="0 0 16 16" aria-hidden="true">
                  <path d="M6.25 3.25L1.5 8l4.75 4.75M2 8h12" />
                </svg>
              </button>
            )}
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
            <div className="chat-status-spacer" />
            {(onClaimRuntime || onReleaseRuntime) && (
              <button
                type="button"
                className={`ghost-btn chat-runtime-btn ${occupyingResources ? 'is-active' : ''} ${claimingRuntime ? 'is-loading' : ''}`}
                disabled={claimingRuntime}
                onClick={() => {
                  if (occupyingResources) {
                    onReleaseRuntime?.()
                  } else {
                    onClaimRuntime?.()
                  }
                }}
                title={
                  claimingRuntime
                    ? 'Claiming runtime — starting Blender and MCP tools for this session...'
                    : occupyingResources
                      ? 'Release runtime — free the Blender slot so another session can use it'
                      : 'Claim runtime — connect this session to a Blender instance for scene operations'
                }
              >
                {claimingRuntime ? (
                  <svg className="spin" width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                    <path d="M14 8a6 6 0 1 1-1.5-4" />
                  </svg>
                ) : (
                  <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="8" cy="8" r="3" />
                    <path d="M8 1v2M8 13v2M1 8h2M13 8h2" />
                  </svg>
                )}
                <span className="chat-runtime-btn-label">
                  {claimingRuntime ? 'Claiming...' : occupyingResources ? 'Release Runtime' : 'Claim Runtime'}
                </span>
              </button>
            )}
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
        {!minimalUi && agentTurnLimitNotice ? (
          <div className="chat-budget-notice" role="status" aria-live="polite">
            <div className="chat-budget-notice-copy">
              <div className="chat-budget-notice-title">Reached the current max agent turns.</div>
              <div className="chat-budget-notice-meta">
                {typeof agentTurnLimitNotice.limit === 'number' && agentTurnLimitNotice.limit > 0
                  ? `Current limit: ${agentTurnLimitNotice.limit}. Increase it to continue this run.`
                  : 'Increase the turn limit to continue this run.'}
              </div>
            </div>
            <button
              type="button"
              className="primary-btn chat-budget-notice-action"
              onClick={() => onContinueAfterAgentTurnLimit?.()}
            >
              Continue
            </button>
          </div>
        ) : null}
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
          providerThinking={providerThinking ?? false}
          providerThinkingAvailable={providerThinkingAvailable}
          onProviderThinkingToggle={onProviderThinkingToggle}
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
          availableReferences={availableSceneObjectReferences}
          pendingReferenceInsertion={pendingReferenceInsertion}
          onPendingReferenceInsertionHandled={onPendingReferenceInsertionHandled}
        />
      </div>
    </div>
  )
}
