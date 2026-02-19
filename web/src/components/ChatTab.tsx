import type { Thread } from '../state/types'
import { MessageList } from './MessageList'
import { ChatComposer } from './ChatComposer'
import { ReferenceImageStrip } from './ReferenceImageStrip'
import { GraphTimeline } from './GraphTimeline'
import type { GraphNodeStream, ReferenceImage, VlmProviderOption } from '../api/types'

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
  vlmLoading?: boolean
  vlmError?: string | null
  vlmLocked?: boolean
  onVlmSelectionChange?: (provider: string, model: string) => void
  graphEvents?: GraphNodeStream[]
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
  vlmLoading = false,
  vlmError = null,
  vlmLocked = false,
  onVlmSelectionChange,
  graphEvents = []
}: ChatTabProps) {
  if (!thread) {
    return <div className="empty-state">Create a conversation to begin.</div>
  }

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
  const availablePrompts = thread.messages.length === 0 ? examplePrompts : []

  return (
    <div className="chat-tab chat-pane">
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
      <GraphTimeline events={graphEvents} />
      <div className="chat-scroll-area">
        <MessageList messages={thread.messages} backendUrl={backendUrl} />
      </div>
      {thread.referenceImages && thread.referenceImages.length > 0 && (
        <ReferenceImageStrip images={thread.referenceImages as ReferenceImage[]} />
      )}
      <ChatComposer
        disabled={isStreaming}
        onSend={onSend}
        onStop={onStop}
        referenceImagesCount={thread.referenceImages?.length ?? 0}
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
        modelLoading={vlmLoading}
        modelError={vlmError}
        modelLocked={selectorDisabled}
      />
    </div>
  )
}
