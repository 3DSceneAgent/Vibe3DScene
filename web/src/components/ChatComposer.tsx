import { useState } from 'react'

type ChatComposerProps = {
  disabled?: boolean
  onSend: (message: string) => void
}

export function ChatComposer({ disabled, onSend }: ChatComposerProps) {
  const [input, setInput] = useState('')

  const handleSend = () => {
    const text = input.trim()
    if (!text) return
    onSend(text)
    setInput('')
  }

  return (
    <div className="composer">
      <textarea
        className="composer-input"
        placeholder="Ask the scene agent…"
        value={input}
        disabled={disabled}
        onChange={(event) => setInput(event.target.value)}
        rows={2}
      />
      <button className="primary-btn" onClick={handleSend} disabled={disabled}>
        Send
      </button>
    </div>
  )
}
