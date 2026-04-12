type SceneObjectIconProps = {
  type: string
  className?: string
}

export function SceneObjectIcon({ type, className = 'scene-tree-icon' }: SceneObjectIconProps) {
  const normalized = type.trim().toUpperCase()

  if (normalized.includes('LIGHT')) {
    return (
      <svg className={className} viewBox="0 0 16 16" aria-hidden="true">
        <circle cx="8" cy="8" r="2.6" />
        <path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.1 3.1l1.4 1.4M11.5 11.5l1.4 1.4M12.9 3.1l-1.4 1.4M4.5 11.5l-1.4 1.4" />
      </svg>
    )
  }

  if (normalized.includes('CAMERA')) {
    return (
      <svg className={className} viewBox="0 0 16 16" aria-hidden="true">
        <path d="M2.5 5.5h7a1.5 1.5 0 0 1 1.5 1.5v2a1.5 1.5 0 0 1-1.5 1.5h-7A1.5 1.5 0 0 1 1 9V7a1.5 1.5 0 0 1 1.5-1.5Z" />
        <path d="m11 7 3-1.8v5.6L11 9" />
      </svg>
    )
  }

  if (normalized.includes('BONE')) {
    return (
      <svg className={className} viewBox="0 0 16 16" aria-hidden="true">
        <path d="M4 5a1.5 1.5 0 1 1 2.3-1.3l3.4 3.4A1.5 1.5 0 1 1 11 9.4l-3.4-3.4A1.5 1.5 0 0 1 4 5Z" />
        <path d="M12 11a1.5 1.5 0 1 1-2.3 1.3L6.3 8.9A1.5 1.5 0 1 1 5 6.6L8.4 10A1.5 1.5 0 0 1 12 11Z" />
      </svg>
    )
  }

  if (
    normalized.includes('GROUP') ||
    normalized.includes('EMPTY') ||
    normalized.includes('OBJECT3D') ||
    normalized.includes('SCENE')
  ) {
    return (
      <svg className={className} viewBox="0 0 16 16" aria-hidden="true">
        <rect x="1.5" y="2" width="5" height="4" rx="1" />
        <rect x="9.5" y="2" width="5" height="4" rx="1" />
        <rect x="9.5" y="10" width="5" height="4" rx="1" />
        <path d="M4 6v2.5h8M11.5 8.5V10" />
      </svg>
    )
  }

  return (
    <svg className={className} viewBox="0 0 16 16" aria-hidden="true">
      <path d="M3 4.5 8 2l5 2.5v7L8 14l-5-2.5z" />
      <path d="M3 4.5 8 7l5-2.5M8 7v7" />
    </svg>
  )
}
