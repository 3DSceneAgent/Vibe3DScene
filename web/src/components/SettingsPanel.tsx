import type { Settings, ThemeId, UiModeId, ViewportThemeId } from '../state/types'

type SettingsPanelProps = {
  settings: Settings
  onChange: (settings: Settings) => void
}

export function SettingsPanel({ settings, onChange }: SettingsPanelProps) {
  const updateTheme = (theme: ThemeId) => {
    onChange({ ...settings, theme })
  }

  const updateViewportTheme = (viewportTheme: ViewportThemeId) => {
    onChange({ ...settings, viewportTheme })
  }

  const updateUiMode = (uiMode: UiModeId) => {
    onChange({ ...settings, uiMode })
  }

  const updateAutoFetchIntervalSeconds = (value: string) => {
    const parsed = Number.parseInt(value, 10)
    const nextInterval = Number.isFinite(parsed) ? Math.min(300, Math.max(1, parsed)) : 10
    onChange({ ...settings, autoFetchIntervalSeconds: nextInterval })
  }

  const updateProviderThinkingDefault = (enabled: boolean) => {
    onChange({ ...settings, providerThinkingDefault: enabled })
  }

  const updateBudget = (
    key: 'maxRequestAgentTurns' | 'maxRequestToolBatches',
    value: string
  ) => {
    const parsed = Number.parseInt(value, 10)
    const nextValue = Number.isFinite(parsed) ? (parsed === -1 ? -1 : Math.max(1, parsed)) : settings[key]
    onChange({ ...settings, [key]: nextValue })
  }

  return (
    <div className="settings-panel">
      <div className="panel">
        <div className="panel-header">
          <div className="panel-title">Backend</div>
        </div>
        <label className="field">
          API Base URL
          <input
            type="text"
            value={settings.backendUrl}
            onChange={(event) => onChange({ ...settings, backendUrl: event.target.value })}
          />
        </label>
      </div>

      <div className="panel">
        <div className="panel-header">
          <div className="panel-title">Theme</div>
        </div>
        <div className="theme-options">
          <button
            className={`theme-card ${settings.theme === 'dark' ? 'active' : ''}`}
            onClick={() => updateTheme('dark')}
          >
            <span className="theme-swatch dark" />
            Dark
          </button>
          <button
            className={`theme-card ${settings.theme === 'light' ? 'active' : ''}`}
            onClick={() => updateTheme('light')}
          >
            <span className="theme-swatch light" />
            Light
          </button>
        </div>
      </div>

      <div className="panel">
        <div className="panel-header">
          <div className="panel-title">Interface</div>
        </div>
        <label className="field checkbox-field">
          Default Thinking
          <label className="toggle-switch compact">
            <input
              type="checkbox"
              checked={settings.providerThinkingDefault}
              onChange={(event) => updateProviderThinkingDefault(event.target.checked)}
            />
            <span className="toggle-slider" />
          </label>
        </label>
        <div className="field">
          <span className="ui-mode-meta">
            New chats inherit this value. Changing the in-chat Thinking toggle also updates this default.
          </span>
        </div>
        <div className="ui-mode-options" role="radiogroup" aria-label="UI Mode">
          <button
            type="button"
            className={`ui-mode-card ${settings.uiMode === 'default' ? 'active' : ''}`}
            onClick={() => updateUiMode('default')}
            aria-pressed={settings.uiMode === 'default'}
          >
            <span className="ui-mode-title">Default</span>
            <span className="ui-mode-meta">Show the full chat and scene controls.</span>
          </button>
          <button
            type="button"
            className={`ui-mode-card ${settings.uiMode === 'minimal' ? 'active' : ''}`}
            onClick={() => updateUiMode('minimal')}
            aria-pressed={settings.uiMode === 'minimal'}
          >
            <span className="ui-mode-title">Minimal</span>
            <span className="ui-mode-meta">Keep the chat pane and 3D viewport as clean as possible.</span>
          </button>
        </div>
      </div>

      <div className="panel">
        <div className="panel-header">
          <div className="panel-title">Viewport</div>
        </div>
        <label className="field checkbox-field">
          Auto-fetch Scene
          <label className="toggle-switch compact">
            <input
              type="checkbox"
              checked={settings.autoRefreshScene}
              onChange={(event) => onChange({ ...settings, autoRefreshScene: event.target.checked })}
            />
            <span className="toggle-slider" />
          </label>
        </label>
        <label className="field">
          Auto-fetch Interval (seconds)
          <input
            type="number"
            min={1}
            max={300}
            step={1}
            value={settings.autoFetchIntervalSeconds}
            onChange={(event) => updateAutoFetchIntervalSeconds(event.target.value)}
          />
        </label>
        <div className="field">
          <span>Viewport Theme</span>
          <div className="viewport-theme-options" role="radiogroup" aria-label="Viewport Theme">
            <button
              type="button"
              className={`viewport-theme-card ${settings.viewportTheme === 'auto' ? 'active' : ''}`}
              onClick={() => updateViewportTheme('auto')}
              aria-pressed={settings.viewportTheme === 'auto'}
            >
              <span className="viewport-theme-title">Auto</span>
              <span className="viewport-theme-meta">Follow UI theme</span>
            </button>
            <button
              type="button"
              className={`viewport-theme-card ${settings.viewportTheme === 'dark' ? 'active' : ''}`}
              onClick={() => updateViewportTheme('dark')}
              aria-pressed={settings.viewportTheme === 'dark'}
            >
              <span className="viewport-theme-title">Dark</span>
              <span className="viewport-theme-meta">Dark viewport</span>
            </button>
            <button
              type="button"
              className={`viewport-theme-card ${settings.viewportTheme === 'light' ? 'active' : ''}`}
              onClick={() => updateViewportTheme('light')}
              aria-pressed={settings.viewportTheme === 'light'}
            >
              <span className="viewport-theme-title">Light</span>
              <span className="viewport-theme-meta">Light viewport</span>
            </button>
          </div>
        </div>
        <label className="field checkbox-field">
          Show Viewport Grid
          <label className="toggle-switch compact">
            <input
              type="checkbox"
              checked={settings.showViewportGrid}
              onChange={(event) => onChange({ ...settings, showViewportGrid: event.target.checked })}
            />
            <span className="toggle-slider" />
          </label>
        </label>
        <label className="field checkbox-field">
          Show Environment Background
          <label className="toggle-switch compact">
            <input
              type="checkbox"
              checked={settings.showHdriBackground}
              onChange={(event) => onChange({ ...settings, showHdriBackground: event.target.checked })}
            />
            <span className="toggle-slider" />
          </label>
        </label>
      </div>

      <div className="panel">
        <div className="panel-header">
          <div className="panel-title">Plan Mode</div>
        </div>
        <div className="field">
          <span className="ui-mode-meta">These limits are used for plan mode requests only.</span>
        </div>
        <label className="field">
          Plan Mode Max Agent Turns
          <input
            type="number"
            min={-1}
            step={1}
            value={settings.maxRequestAgentTurns}
            onChange={(event) => updateBudget('maxRequestAgentTurns', event.target.value)}
          />
        </label>
        <label className="field">
          Plan Mode Max Tool Batches
          <input
            type="number"
            min={-1}
            step={1}
            value={settings.maxRequestToolBatches}
            onChange={(event) => updateBudget('maxRequestToolBatches', event.target.value)}
          />
        </label>
      </div>
    </div>
  )
}
