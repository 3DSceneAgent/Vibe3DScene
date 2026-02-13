export function resolveMediaUrl(src: string | undefined, backendUrl: string): string | undefined {
  if (!src) return undefined
  const trimmedSrc = src.trim()
  if (!trimmedSrc) return undefined

  if (/^(?:https?:\/\/|data:|blob:|file:\/\/)/i.test(trimmedSrc)) {
    return trimmedSrc
  }

  const trimmedBackendUrl = backendUrl.trim()
  if (!trimmedBackendUrl) {
    return trimmedSrc
  }

  try {
    return new URL(trimmedSrc, withTrailingSlash(trimmedBackendUrl)).toString()
  } catch {
    return trimmedSrc
  }
}


function withTrailingSlash(input: string): string {
  return input.endsWith('/') ? input : `${input}/`
}
