import { useEffect, useMemo, useRef, type ReactNode } from 'react'
// @ts-expect-error project does not include three type declarations in this workspace.
import * as THREE from 'three'
// @ts-expect-error project does not include three example type declarations in this workspace.
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader'
// @ts-expect-error project does not include three example type declarations in this workspace.
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls'
import type { SceneHierarchyNode } from '../state/types'

type EnvironmentPreset = 'studio' | 'warm' | 'cool'
type ViewportTheme = 'auto' | 'dark' | 'light'
type UiTheme = 'dark' | 'light'

type GltfViewerProps = {
  gltfUrl: string | null
  environment: EnvironmentPreset
  viewportTheme?: ViewportTheme
  uiTheme?: UiTheme
  onHierarchyChange?: (nodes: SceneHierarchyNode[]) => void
  isFullscreen?: boolean
  onToggleFullscreen?: () => void
  headerControls?: ReactNode
  alwaysAutoFrameCamera?: boolean
}

const environmentPresets: Record<EnvironmentPreset, { ambient: number; directional: number; color: string }> = {
  studio: { ambient: 0.55, directional: 1.2, color: '#ffffff' },
  warm: { ambient: 0.6, directional: 1.1, color: '#ffd8b2' },
  cool: { ambient: 0.5, directional: 1.3, color: '#cfe6ff' }
}

const viewportPalettes: Record<
  UiTheme,
  {
    background: number
    defaultGridMajor: number
    defaultGridMinor: number
    modelGridMajor: number
    modelGridMinor: number
  }
> = {
  dark: {
    background: 0x0f1117,
    defaultGridMajor: 0x36425a,
    defaultGridMinor: 0x1e2534,
    modelGridMajor: 0x3a4865,
    modelGridMinor: 0x1e2534
  },
  light: {
    background: 0xf3f6fb,
    defaultGridMajor: 0xb4c3d8,
    defaultGridMinor: 0xd6dfeb,
    modelGridMajor: 0xa8bad2,
    modelGridMinor: 0xd6dfeb
  }
}

function buildHierarchy(object: THREE.Object3D): SceneHierarchyNode {
  return {
    id: object.uuid,
    name: object.name.trim() || object.type,
    type: object.type,
    children: object.children.map((child: THREE.Object3D) => buildHierarchy(child))
  }
}

function disposeMaterial(material: THREE.Material | THREE.Material[]): void {
  if (Array.isArray(material)) {
    material.forEach((entry) => disposeMaterial(entry))
    return
  }
  Object.values(material as Record<string, unknown>).forEach((entry: unknown) => {
    if (entry && typeof entry === 'object') {
      const maybeTexture = entry as { isTexture?: boolean; dispose?: () => void }
      if (maybeTexture.isTexture && typeof maybeTexture.dispose === 'function') {
        maybeTexture.dispose()
      }
    }
  })
  material.dispose()
}

function disposeObject3D(object: THREE.Object3D | null): void {
  if (!object) return
  object.traverse((entry: THREE.Object3D) => {
    const geometry = (entry as { geometry?: THREE.BufferGeometry }).geometry
    if (geometry) {
      geometry.dispose()
    }
    const material = (entry as { material?: THREE.Material | THREE.Material[] }).material
    if (material) {
      disposeMaterial(material)
    }
  })
}

export function GltfViewer({
  gltfUrl,
  environment,
  viewportTheme = 'auto',
  uiTheme = 'dark',
  onHierarchyChange,
  isFullscreen = false,
  onToggleFullscreen,
  headerControls,
  alwaysAutoFrameCamera = false
}: GltfViewerProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const sceneRef = useRef<THREE.Scene | null>(null)
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null)
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null)
  const controlsRef = useRef<OrbitControls | null>(null)
  const modelRef = useRef<THREE.Object3D | null>(null)
  const gridRef = useRef<THREE.GridHelper | null>(null)
  const lightRef = useRef<{ ambient: THREE.AmbientLight; directional: THREE.DirectionalLight } | null>(
    null
  )
  const loadTokenRef = useRef(0)
  const hasLoadedModelRef = useRef(false)
  const cameraViewRef = useRef<{ position: THREE.Vector3; target: THREE.Vector3 } | null>(null)

  const preset = useMemo(() => environmentPresets[environment], [environment])
  const resolvedViewportTheme: UiTheme =
    viewportTheme === 'auto' ? uiTheme : viewportTheme
  const viewportPalette = useMemo(
    () => viewportPalettes[resolvedViewportTheme],
    [resolvedViewportTheme]
  )
  const viewportPaletteRef = useRef(viewportPalette)
  const onHierarchyChangeRef = useRef(onHierarchyChange)
  const alwaysAutoFrameCameraRef = useRef(alwaysAutoFrameCamera)

  useEffect(() => {
    viewportPaletteRef.current = viewportPalette
  }, [viewportPalette])

  useEffect(() => {
    onHierarchyChangeRef.current = onHierarchyChange
  }, [onHierarchyChange])

  useEffect(() => {
    alwaysAutoFrameCameraRef.current = alwaysAutoFrameCamera
  }, [alwaysAutoFrameCamera])

  useEffect(() => {
    if (!containerRef.current) return

    const container = containerRef.current
    const scene = new THREE.Scene()
    const palette = viewportPaletteRef.current
    scene.background = new THREE.Color(palette.background)

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    renderer.outputColorSpace = THREE.SRGBColorSpace
    renderer.toneMapping = THREE.ACESFilmicToneMapping
    renderer.toneMappingExposure = 1.15
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))

    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 4000)
    camera.position.set(9, 7, 9)

    const ambient = new THREE.AmbientLight('#ffffff', 0.55)
    const directional = new THREE.DirectionalLight('#ffffff', 1.2)
    directional.position.set(8, 14, 6)
    scene.add(ambient, directional)

    const defaultGrid = new THREE.GridHelper(
      20,
      40,
      palette.defaultGridMajor,
      palette.defaultGridMinor
    )
    defaultGrid.position.y = -0.01
    scene.add(defaultGrid)

    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.08
    controls.target.set(0, 0.5, 0)
    controls.update()
    const syncViewState = () => {
      cameraViewRef.current = {
        position: camera.position.clone(),
        target: controls.target.clone()
      }
    }
    syncViewState()
    controls.addEventListener('change', syncViewState)

    const resizeRenderer = () => {
      const width = container.clientWidth
      const height = container.clientHeight
      if (width <= 0 || height <= 0) return
      renderer.setSize(width, height)
      camera.aspect = width / height
      camera.updateProjectionMatrix()
    }

    resizeRenderer()
    container.appendChild(renderer.domElement)

    const resizeObserver = new ResizeObserver(() => {
      resizeRenderer()
    })
    resizeObserver.observe(container)

    sceneRef.current = scene
    rendererRef.current = renderer
    cameraRef.current = camera
    controlsRef.current = controls
    gridRef.current = defaultGrid
    lightRef.current = { ambient, directional }

    let animationFrame = 0
    const animate = () => {
      controls.update()
      renderer.render(scene, camera)
      animationFrame = window.requestAnimationFrame(animate)
    }
    animate()

    return () => {
      window.cancelAnimationFrame(animationFrame)
      resizeObserver.disconnect()
      controls.removeEventListener('change', syncViewState)
      controls.dispose()
      disposeObject3D(modelRef.current)
      modelRef.current = null
      disposeObject3D(gridRef.current)
      gridRef.current = null
      renderer.dispose()
      if (renderer.domElement.parentElement) {
        renderer.domElement.parentElement.removeChild(renderer.domElement)
      }
      scene.clear()
      hasLoadedModelRef.current = false
      cameraViewRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!lightRef.current) return
    const { ambient, directional } = lightRef.current
    ambient.color = new THREE.Color(preset.color)
    directional.color = new THREE.Color(preset.color)
    ambient.intensity = preset.ambient
    directional.intensity = preset.directional
  }, [preset.ambient, preset.color, preset.directional])

  useEffect(() => {
    const scene = sceneRef.current
    if (!scene) return

    scene.background = new THREE.Color(viewportPalette.background)

    if (gridRef.current) {
      disposeObject3D(gridRef.current)
      scene.remove(gridRef.current)
    }

    let nextGrid: THREE.GridHelper
    if (modelRef.current) {
      const box = new THREE.Box3().setFromObject(modelRef.current)
      const size = new THREE.Vector3()
      box.getSize(size)
      const maxDim = Math.max(size.x, size.y, size.z, 0.1)
      const gridSize = Math.max(20, Math.ceil(maxDim * 2))
      const divisions = Math.max(20, Math.min(160, gridSize * 2))
      nextGrid = new THREE.GridHelper(
        gridSize,
        divisions,
        viewportPalette.modelGridMajor,
        viewportPalette.modelGridMinor
      )
      nextGrid.position.y = box.min.y - 0.001
    } else {
      nextGrid = new THREE.GridHelper(
        20,
        40,
        viewportPalette.defaultGridMajor,
        viewportPalette.defaultGridMinor
      )
      nextGrid.position.y = -0.01
    }

    scene.add(nextGrid)
    gridRef.current = nextGrid
  }, [viewportPalette])

  useEffect(() => {
    const scene = sceneRef.current
    const camera = cameraRef.current
    const controls = controlsRef.current
    if (!scene || !camera || !controls) return

    const resetToDefault = () => {
      if (modelRef.current) {
        disposeObject3D(modelRef.current)
        scene.remove(modelRef.current)
        modelRef.current = null
      }
      if (gridRef.current) {
        disposeObject3D(gridRef.current)
        scene.remove(gridRef.current)
      }
      const palette = viewportPaletteRef.current
      const nextGrid = new THREE.GridHelper(
        20,
        40,
        palette.defaultGridMajor,
        palette.defaultGridMinor
      )
      nextGrid.position.y = -0.01
      scene.add(nextGrid)
      gridRef.current = nextGrid
      camera.position.set(9, 7, 9)
      camera.near = 0.1
      camera.far = 4000
      camera.updateProjectionMatrix()
      controls.target.set(0, 0.5, 0)
      controls.update()
      hasLoadedModelRef.current = false
      cameraViewRef.current = {
        position: camera.position.clone(),
        target: controls.target.clone()
      }
      onHierarchyChangeRef.current?.([])
    }

    if (!gltfUrl) {
      resetToDefault()
      return
    }

    const loader = new GLTFLoader()
    const loadToken = loadTokenRef.current + 1
    loadTokenRef.current = loadToken
    const preservedView =
      hasLoadedModelRef.current && !alwaysAutoFrameCameraRef.current ? cameraViewRef.current : null

    loader.load(
      gltfUrl,
      (gltf: { scene: THREE.Object3D }) => {
        if (loadTokenRef.current !== loadToken) return

        if (modelRef.current) {
          disposeObject3D(modelRef.current)
          scene.remove(modelRef.current)
        }
        modelRef.current = gltf.scene
        scene.add(gltf.scene)

        const box = new THREE.Box3().setFromObject(gltf.scene)
        const size = new THREE.Vector3()
        const center = new THREE.Vector3()
        box.getSize(size)
        box.getCenter(center)
        const maxDim = Math.max(size.x, size.y, size.z, 0.1)

        if (gridRef.current) {
          disposeObject3D(gridRef.current)
          scene.remove(gridRef.current)
        }
        const gridSize = Math.max(20, Math.ceil(maxDim * 2))
        const divisions = Math.max(20, Math.min(160, gridSize * 2))
        const palette = viewportPaletteRef.current
        const nextGrid = new THREE.GridHelper(
          gridSize,
          divisions,
          palette.modelGridMajor,
          palette.modelGridMinor
        )
        nextGrid.position.y = box.min.y - 0.001
        scene.add(nextGrid)
        gridRef.current = nextGrid

        if (preservedView) {
          camera.position.copy(preservedView.position)
          controls.target.copy(preservedView.target)
          const distance = camera.position.distanceTo(controls.target)
          camera.near = Math.max(distance / 1000, 0.05)
          camera.far = Math.max(distance * 25, 1000)
          camera.updateProjectionMatrix()
          controls.update()
        } else {
          const fov = (camera.fov * Math.PI) / 180
          const fitHeightDistance = maxDim / (2 * Math.tan(fov / 2))
          const fitWidthDistance = fitHeightDistance / Math.max(camera.aspect, 0.5)
          const distance = Math.max(fitHeightDistance, fitWidthDistance) * 1.45
          const direction = new THREE.Vector3(1, 0.65, 1).normalize()
          camera.position.copy(center).addScaledVector(direction, distance)
          camera.near = Math.max(distance / 1000, 0.05)
          camera.far = Math.max(distance * 25, 1000)
          camera.updateProjectionMatrix()
          controls.target.copy(center)
          controls.update()
        }
        hasLoadedModelRef.current = true
        cameraViewRef.current = {
          position: camera.position.clone(),
          target: controls.target.clone()
        }

        const roots =
          gltf.scene.children.length > 0
            ? gltf.scene.children.map((child: THREE.Object3D) => buildHierarchy(child))
            : [buildHierarchy(gltf.scene)]
        onHierarchyChangeRef.current?.(roots)
      },
      undefined,
      (error: unknown) => {
        console.error('Failed to load GLTF', error)
        if (loadTokenRef.current === loadToken) {
          onHierarchyChangeRef.current?.([])
        }
      }
    )
  }, [gltfUrl])

  return (
    <div className={`viewer-shell ${isFullscreen ? 'is-fullscreen' : ''}`}>
      <div className="viewer-header">
        <div className="panel-title">3D Viewport</div>
        <div className="viewer-header-right">
          <div className="panel-subtitle"> </div>
          {headerControls}
          {onToggleFullscreen && (
            <button className="ghost-btn viewer-fullscreen-btn" onClick={onToggleFullscreen}>
              {isFullscreen ? 'Exit' : 'Fullscreen'}
            </button>
          )}
        </div>
      </div>
      <div className={`viewer viewport-${resolvedViewportTheme}`}>
        {!gltfUrl && <div className="viewer-placeholder">No model loaded</div>}
        <div className="viewer-canvas" ref={containerRef} />
      </div>
    </div>
  )
}
