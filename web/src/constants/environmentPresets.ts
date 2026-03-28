export type EnvironmentPreset =
  | 'none'
  | 'skylight'
  | 'studio'
  | 'sunset'
  | 'daylight'
  | 'overcast'
  | 'workshop'

export type ProceduralSkyConfig = {
  turbidity: number
  rayleigh: number
  mieCoefficient: number
  mieDirectionalG: number
  elevation: number
  azimuth: number
  skyColor: string
  groundColor: string
  hemisphereIntensity: number
  sunColor: string
  sunIntensity: number
  environmentIntensity: number
}

export type EnvironmentPresetConfig = {
  label: string
  description: string
  ambient: number
  directional: number
  color: string
  exposure: number
  hdriUrl: string | null
  sourceUrl: string | null
  proceduralSky?: ProceduralSkyConfig | null
}

export const DEFAULT_ENVIRONMENT_PRESET: EnvironmentPreset = 'studio'

// Viewport presets may use bundled HDRIs or a procedural sky so lighting works offline.
export const environmentPresets: Record<EnvironmentPreset, EnvironmentPresetConfig> = {
  none: {
    label: 'None',
    description: 'Disable HDRI environment lighting and keep only basic viewport fill lights.',
    ambient: 0.24,
    directional: 0.88,
    color: '#ffffff',
    exposure: 1.0,
    hdriUrl: null,
    sourceUrl: null,
    proceduralSky: null
  },
  skylight: {
    label: 'Skylight',
    description: 'Procedural daylight sky with PMREM-based environment lighting and no HDRI asset.',
    ambient: 0.02,
    directional: 0.92,
    color: '#dcefff',
    exposure: 0.88,
    hdriUrl: null,
    sourceUrl: null,
    proceduralSky: {
      turbidity: 3.8,
      rayleigh: 1.9,
      mieCoefficient: 0.018,
      mieDirectionalG: 0.84,
      elevation: 42,
      azimuth: 132,
      skyColor: '#dcefff',
      groundColor: '#4d5563',
      hemisphereIntensity: 0.54,
      sunColor: '#fff2d6',
      sunIntensity: 0.88,
      environmentIntensity: 0.48
    }
  },
  studio: {
    label: 'Studio',
    description: 'Neutral softbox studio lighting for product-style previews.',
    ambient: 0.26,
    directional: 0.95,
    color: '#ffffff',
    exposure: 1.0,
    hdriUrl: '/hdri/poly_haven_studio_1k.hdr',
    sourceUrl: 'https://polyhaven.com/a/poly_haven_studio',
    proceduralSky: null
  },
  sunset: {
    label: 'Sunset',
    description: 'Warm late-day highlights with richer contrast and reflections.',
    ambient: 0.24,
    directional: 0.9,
    color: '#ffd9bc',
    exposure: 0.95,
    hdriUrl: '/hdri/venice_sunset_1k.hdr',
    sourceUrl: 'https://polyhaven.com/a/venice_sunset',
    proceduralSky: null
  },
  daylight: {
    label: 'Daylight',
    description: 'Clear outdoor daylight with cooler white balance and crisp speculars.',
    ambient: 0.25,
    directional: 0.92,
    color: '#d6e8ff',
    exposure: 1.0,
    hdriUrl: '/hdri/syferfontein_1d_clear_1k.hdr',
    sourceUrl: 'https://polyhaven.com/a/syferfontein_1d_clear',
    proceduralSky: null
  },
  overcast: {
    label: 'Overcast',
    description: 'Soft city overcast lighting with low harsh shadows.',
    ambient: 0.28,
    directional: 0.78,
    color: '#edf3ff',
    exposure: 0.92,
    hdriUrl: '/hdri/plac_wolnosci_1k.hdr',
    sourceUrl: 'https://polyhaven.com/a/plac_wolnosci',
    proceduralSky: null
  },
  workshop: {
    label: 'Workshop',
    description: 'Industrial interior reflections for harder metallic reads.',
    ambient: 0.22,
    directional: 0.82,
    color: '#ffe5d2',
    exposure: 0.9,
    hdriUrl: '/hdri/aerodynamics_workshop_1k.hdr',
    sourceUrl: 'https://polyhaven.com/a/aerodynamics_workshop',
    proceduralSky: null
  }
}

export const environmentPresetOptions: Array<{
  value: EnvironmentPreset
  label: string
  description: string
}> = [
  {
    value: 'none',
    label: environmentPresets.none.label,
    description: environmentPresets.none.description
  },
  {
    value: 'skylight',
    label: environmentPresets.skylight.label,
    description: environmentPresets.skylight.description
  },
  {
    value: 'studio',
    label: environmentPresets.studio.label,
    description: environmentPresets.studio.description
  },
  {
    value: 'sunset',
    label: environmentPresets.sunset.label,
    description: environmentPresets.sunset.description
  },
  {
    value: 'daylight',
    label: environmentPresets.daylight.label,
    description: environmentPresets.daylight.description
  },
  {
    value: 'overcast',
    label: environmentPresets.overcast.label,
    description: environmentPresets.overcast.description
  },
  {
    value: 'workshop',
    label: environmentPresets.workshop.label,
    description: environmentPresets.workshop.description
  }
]
