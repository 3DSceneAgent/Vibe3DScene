type RawExampleJson = {
  prompt?: unknown
  fast_mode?: unknown
  reference_images?: unknown
  vlm_provider?: unknown
  vlm_model?: unknown
  provider_thinking?: unknown
  provider?: unknown
  model?: unknown
  thinking?: unknown
}

export type ExamplePromptEntry = {
  id: string
  title: string
  prompt: string
}

export type ExampleReferenceImage = {
  path: string
  label: string
  url: string | null
}

export type ConsoleExample = {
  id: string
  title: string
  prompt: string
  fastMode: boolean
  referenceImages: ExampleReferenceImage[]
  vlmProvider?: string
  vlmModel?: string
  providerThinking?: boolean
}

function humanizeBasename(basename: string): string {
  return basename
    .split(/[_-]+/)
    .filter(Boolean)
    .map((segment) => segment.charAt(0).toUpperCase() + segment.slice(1))
    .join(' ')
}

function normalizePrompt(raw: string): string {
  return raw
    .replace(/\r\n/g, '\n')
    .trim()
}

function normalizeOptionalString(raw: unknown, { lower = false }: { lower?: boolean } = {}): string | undefined {
  if (typeof raw !== 'string') {
    return undefined
  }
  const normalized = raw.trim()
  if (!normalized) {
    return undefined
  }
  return lower ? normalized.toLowerCase() : normalized
}

function normalizeOptionalBoolean(raw: unknown): boolean | undefined {
  if (typeof raw === 'boolean') {
    return raw
  }
  return undefined
}

function basenameFromPath(path: string): string {
  const match = path.match(/([^/]+)\.[^/.]+$/)
  return match?.[1] ?? path
}

const markdownModules = import.meta.glob('../../../assets/examples/*.md', {
  eager: true,
  import: 'default',
  query: '?raw'
}) as Record<string, string>

const jsonModules = import.meta.glob('../../../assets/examples/*.json', {
  eager: true,
  import: 'default'
}) as Record<string, RawExampleJson>

const imageModules = import.meta.glob('../../../assets/example_images/*', {
  eager: true,
  import: 'default'
}) as Record<string, string>

function resolveImageUrl(relativePath: string): string | null {
  const normalizedTarget = relativePath.replace(/\\/g, '/').split('/').pop()
  if (!normalizedTarget) {
    return null
  }
  const matchedEntry = Object.entries(imageModules).find(([imagePath]) => imagePath.endsWith(`/${normalizedTarget}`))
  return matchedEntry?.[1] ?? null
}

export const EXAMPLE_PROMPT_ENTRIES: ExamplePromptEntry[] = Object.entries(markdownModules)
  .map(([path, content]) => {
    const basename = basenameFromPath(path)
    return {
      id: basename,
      title: humanizeBasename(basename),
      prompt: normalizePrompt(content)
    }
  })
  .filter((entry) => entry.prompt.length > 0)
  .sort((left, right) => left.title.localeCompare(right.title))

export const EXAMPLE_PROMPTS: string[] = EXAMPLE_PROMPT_ENTRIES.map((entry) => entry.prompt)

export const CONSOLE_EXAMPLES: ConsoleExample[] = Object.entries(jsonModules)
  .map(([path, raw]) => {
    const basename = basenameFromPath(path)
    const prompt = typeof raw.prompt === 'string' ? normalizePrompt(raw.prompt) : ''
    const referenceImagePaths = Array.isArray(raw.reference_images)
      ? raw.reference_images.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
      : []
    const vlmProvider = normalizeOptionalString(raw.vlm_provider ?? raw.provider, { lower: true })
    const vlmModel = normalizeOptionalString(raw.vlm_model ?? raw.model)
    const providerThinking = normalizeOptionalBoolean(raw.provider_thinking ?? raw.thinking)
    return {
      id: basename,
      title: humanizeBasename(basename),
      prompt,
      fastMode: raw.fast_mode === true,
      vlmProvider,
      vlmModel,
      providerThinking,
      referenceImages: referenceImagePaths.map((imagePath) => ({
        path: imagePath,
        label: imagePath.split('/').pop() ?? imagePath,
        url: resolveImageUrl(imagePath)
      }))
    }
  })
  .filter((entry) => entry.prompt.length > 0)
  .sort((left, right) => left.title.localeCompare(right.title))
