import type { Settings, ThemeId } from '../state/types'

type SettingsPanelProps = {
  settings: Settings
  onChange: (settings: Settings) => void
}

export function SettingsPanel({ settings, onChange }: SettingsPanelProps) {
  const updateTheme = (theme: ThemeId) => {
    onChange({ ...settings, theme })
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
    </div>
  )
}
