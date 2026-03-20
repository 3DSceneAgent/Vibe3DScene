import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
// @ts-expect-error project does not include three type declarations in this workspace.
import * as THREE from 'three'
// @ts-expect-error project does not include three example type declarations in this workspace.
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader'
// @ts-expect-error project does not include three example type declarations in this workspace.
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls'
// @ts-expect-error project does not include three example type declarations in this workspace.
import { RGBELoader } from 'three/examples/jsm/loaders/RGBELoader'
import type { SceneHierarchyNode } from '../state/types'

type EnvironmentPreset = 'studio' | 'warm' | 'cool'
type ViewportTheme = 'auto' | 'dark' | 'light'
type UiTheme = 'dark' | 'light'

type GltfViewerProps = {
  gltfUrl: string | null
  environment: EnvironmentPreset
  viewportTheme?: ViewportTheme
  uiTheme?: UiTheme
  twoSidedRendering?: boolean
  onHierarchyChange?: (nodes: SceneHierarchyNode[]) => void
  isFullscreen?: boolean
  onToggleFullscreen?: () => void
  showFullscreenButton?: boolean
  headerControls?: ReactNode
  headerTrailingControls?: ReactNode
  alwaysAutoFrameCamera?: boolean
}

type EnvironmentPresetConfig = {
  ambient: number
  directional: number
  color: string
  exposure: number
  hdriUrl: string
}

const environmentPresets: Record<EnvironmentPreset, EnvironmentPresetConfig> = {
  studio: {
    ambient: 0.26,
    directional: 0.95,
    color: '#ffffff',
    exposure: 1.0,
    hdriUrl:
      'https://cdn.jsdelivr.net/gh/mrdoob/three.js@dev/examples/textures/equirectangular/quarry_01_1k.hdr'
  },
  warm: {
    ambient: 0.24,
    directional: 0.9,
    color: '#ffd9bc',
    exposure: 0.95,
    hdriUrl:
      'https://cdn.jsdelivr.net/gh/mrdoob/three.js@dev/examples/textures/equirectangular/venice_sunset_1k.hdr'
  },
  cool: {
    ambient: 0.25,
    directional: 0.92,
    color: '#cfe6ff',
    exposure: 1.02,
    hdriUrl:
      'https://cdn.jsdelivr.net/gh/mrdoob/three.js@dev/examples/textures/equirectangular/blouberg_sunrise_2_1k.hdr'
  }
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

function getAutoFrameDirection(size: THREE.Vector3): THREE.Vector3 {
  const xToZRatio = size.x / Math.max(size.z, 0.001)
  if (xToZRatio > 1.35) {
    return new THREE.Vector3(0.75, 0.6, 1.25).normalize()
  }
  if (xToZRatio < 0.74) {
    return new THREE.Vector3(1.25, 0.6, 0.75).normalize()
  }
  return new THREE.Vector3(1, 0.62, 1).normalize()
}

function frameCameraToBox(
  camera: THREE.PerspectiveCamera,
  controls: OrbitControls,
  box: THREE.Box3,
  size: THREE.Vector3,
  center: THREE.Vector3
): void {
  const sphere = box.getBoundingSphere(new THREE.Sphere())
  const radius = Math.max(sphere.radius, 0.1)
  const verticalFov = (camera.fov * Math.PI) / 180
  const horizontalFov = 2 * Math.atan(Math.tan(verticalFov / 2) * Math.max(camera.aspect, 0.1))
  const limitingFov = Math.max(Math.PI / 18, Math.min(verticalFov, horizontalFov))
  const distance = (radius / Math.sin(limitingFov / 2)) * 1.22
  const direction = getAutoFrameDirection(size)
  const focus = center.clone()
  focus.y = box.min.y + size.y * 0.45

  camera.position.copy(focus).addScaledVector(direction, distance)
  camera.near = Math.max(distance / 1000, radius / 200, 0.01)
  camera.far = Math.max(distance + radius * 10, 1000)
  camera.updateProjectionMatrix()
  controls.target.copy(focus)
  controls.update()
}

function buildHierarchy(object: THREE.Object3D): SceneHierarchyNode {
  return {
    id: object.uuid,
    name: object.name.trim() || object.type,
    type: object.type,
    children: object.children.map((child: THREE.Object3D) => buildHierarchy(child))
  }
}

function sceneContainsLights(object: THREE.Object3D): boolean {
  let hasLights = false
  object.traverse((entry: THREE.Object3D) => {
    if (hasLights) return
    if ((entry as { isLight?: boolean }).isLight) {
      hasLights = true
    }
  })
  return hasLights
}

function collectSceneCameras(object: THREE.Object3D): THREE.Camera[] {
  const cameras: THREE.Camera[] = []
  object.traverse((entry: THREE.Object3D) => {
    if ((entry as { isCamera?: boolean }).isCamera) {
      cameras.push(entry as THREE.Camera)
    }
  })
  return cameras
}

function pickPreferredSceneCamera(cameras: THREE.Camera[]): THREE.Camera | null {
  if (cameras.length === 0) {
    return null
  }
  const normalized = cameras
    .map((camera) => ({
      camera,
      name: (camera.name || '').trim().toLowerCase()
    }))
    .filter((entry) => entry.name.length > 0)

  const exact = normalized.find((entry) => entry.name === 'scenecamera_ne')
  if (exact) return exact.camera

  const sceneNamed = normalized.find((entry) => entry.name.startsWith('scenecamera_'))
  if (sceneNamed) return sceneNamed.camera

  return cameras[0]
}

function applyExportedCameraView(
  sourceCamera: THREE.Camera,
  targetCamera: THREE.PerspectiveCamera,
  controls: OrbitControls,
  boxCenter: THREE.Vector3,
  boxSize: THREE.Vector3
): void {
  sourceCamera.updateMatrixWorld(true)

  const worldPosition = new THREE.Vector3()
  const worldQuaternion = new THREE.Quaternion()
  sourceCamera.getWorldPosition(worldPosition)
  sourceCamera.getWorldQuaternion(worldQuaternion)

  targetCamera.position.copy(worldPosition)
  targetCamera.quaternion.copy(worldQuaternion)

  if ((sourceCamera as { isPerspectiveCamera?: boolean }).isPerspectiveCamera) {
    const perspective = sourceCamera as THREE.PerspectiveCamera
    targetCamera.fov = perspective.fov
    targetCamera.near = Math.max(perspective.near, 0.01)
    targetCamera.far = Math.max(perspective.far, targetCamera.near + 1)
  } else {
    targetCamera.near = Math.max(targetCamera.near, 0.01)
    targetCamera.far = Math.max(targetCamera.far, targetCamera.near + 1)
  }
  targetCamera.updateProjectionMatrix()

  const forward = new THREE.Vector3(0, 0, -1).applyQuaternion(worldQuaternion).normalize()
  const toCenter = boxCenter.clone().sub(worldPosition)
  const centerDistance = toCenter.length()
  const fallbackDistance = Math.max(boxSize.length() * 0.35, 1)
  const lookDistance = Math.max(centerDistance, fallbackDistance)
  const lookTarget = worldPosition.clone().addScaledVector(forward, lookDistance)

  if (centerDistance > 0.001) {
    const towardCenter = toCenter.normalize()
    if (forward.dot(towardCenter) > 0.15) {
      lookTarget.copy(boxCenter)
    }
  }

  controls.target.copy(lookTarget)
  controls.update()
}

function normalizeEmbeddedLightIntensities(object: THREE.Object3D): void {
  const lights: THREE.Light[] = []
  object.traverse((entry: THREE.Object3D) => {
    if ((entry as { isLight?: boolean }).isLight) {
      lights.push(entry as THREE.Light)
    }
  })
  if (lights.length === 0) return

  const intensities = lights
    .map((light) => (Number.isFinite(light.intensity) ? light.intensity : 0))
    .filter((intensity) => intensity > 0)
  if (intensities.length === 0) return

  const maxIntensity = Math.max(...intensities)
  const targetMaxIntensity = 40
  if (maxIntensity <= targetMaxIntensity) return

  const scale = targetMaxIntensity / maxIntensity
  lights.forEach((light) => {
    if (!Number.isFinite(light.intensity) || light.intensity <= 0) return
    light.intensity *= scale
  })
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

function visitMaterials(
  material: THREE.Material | THREE.Material[] | undefined,
  visitor: (entry: THREE.Material) => void
): void {
  if (!material) return
  if (Array.isArray(material)) {
    material.forEach((entry) => visitor(entry))
    return
  }
  visitor(material)
}

function applyTwoSidedRenderingState(
  object: THREE.Object3D | null,
  enabled: boolean,
  originalSides: WeakMap<THREE.Material, number>
): void {
  if (!object) return
  object.traverse((entry: THREE.Object3D) => {
    const material = (entry as { material?: THREE.Material | THREE.Material[] }).material
    visitMaterials(material, (currentMaterial) => {
      if (!originalSides.has(currentMaterial)) {
        originalSides.set(currentMaterial, currentMaterial.side as number)
      }
      const originalSide = originalSides.get(currentMaterial)
      if (typeof originalSide !== 'number') return

      const nextSide = enabled ? THREE.DoubleSide : originalSide
      if (currentMaterial.side === nextSide) return

      currentMaterial.side = nextSide as THREE.Side
      currentMaterial.needsUpdate = true
    })
  })
}

export function GltfViewer({
  gltfUrl,
  environment,
  viewportTheme = 'auto',
  uiTheme = 'dark',
  twoSidedRendering = false,
  onHierarchyChange,
  isFullscreen = false,
  onToggleFullscreen,
  showFullscreenButton = true,
  headerControls,
  headerTrailingControls,
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
  const pmremGeneratorRef = useRef<THREE.PMREMGenerator | null>(null)
  const environmentRenderTargetRef = useRef<THREE.WebGLRenderTarget | null>(null)
  const environmentLoadTokenRef = useRef(0)
  const loadTokenRef = useRef(0)
  const hasLoadedModelRef = useRef(false)
  const cameraViewRef = useRef<{ position: THREE.Vector3; target: THREE.Vector3 } | null>(null)
  const hasUserCameraOverrideRef = useRef(false)
  const resizeRendererRef = useRef<(() => void) | null>(null)
  const originalMaterialSidesRef = useRef<WeakMap<THREE.Material, number>>(new WeakMap())
  const twoSidedRenderingRef = useRef(twoSidedRendering)
  const [useFallbackLighting, setUseFallbackLighting] = useState(true)

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
    twoSidedRenderingRef.current = twoSidedRendering
    applyTwoSidedRenderingState(modelRef.current, twoSidedRendering, originalMaterialSidesRef.current)
  }, [twoSidedRendering])

  useEffect(() => {
    if (alwaysAutoFrameCamera) {
      hasUserCameraOverrideRef.current = false
    }
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
    const pmremGenerator = new THREE.PMREMGenerator(renderer)
    pmremGenerator.compileEquirectangularShader()

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
    const markUserCameraOverride = () => {
      hasUserCameraOverrideRef.current = true
    }
    syncViewState()
    controls.addEventListener('start', markUserCameraOverride)
    controls.addEventListener('change', syncViewState)

    const resizeRenderer = () => {
      const width = container.clientWidth
      const height = container.clientHeight
      if (width <= 0 || height <= 0) return
      renderer.setSize(width, height)
      camera.aspect = width / height
      camera.updateProjectionMatrix()
    }
    resizeRendererRef.current = resizeRenderer

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
    pmremGeneratorRef.current = pmremGenerator

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
      environmentLoadTokenRef.current += 1
      controls.removeEventListener('change', syncViewState)
      controls.removeEventListener('start', markUserCameraOverride)
      controls.dispose()
      if (environmentRenderTargetRef.current) {
        environmentRenderTargetRef.current.dispose()
        environmentRenderTargetRef.current = null
      }
      pmremGenerator.dispose()
      pmremGeneratorRef.current = null
      disposeObject3D(modelRef.current)
      modelRef.current = null
      disposeObject3D(gridRef.current)
      gridRef.current = null
      renderer.dispose()
      if (renderer.domElement.parentElement) {
        renderer.domElement.parentElement.removeChild(renderer.domElement)
      }
      scene.clear()
      resizeRendererRef.current = null
      hasLoadedModelRef.current = false
      cameraViewRef.current = null
      hasUserCameraOverrideRef.current = false
    }
  }, [])

  useEffect(() => {
    const resize = resizeRendererRef.current
    if (!resize) return

    const frame = window.requestAnimationFrame(() => {
      resize()
      const renderer = rendererRef.current
      const scene = sceneRef.current
      const camera = cameraRef.current
      if (renderer && scene && camera) {
        renderer.render(scene, camera)
      }
    })
    return () => {
      window.cancelAnimationFrame(frame)
    }
  }, [gltfUrl, isFullscreen])

  useEffect(() => {
    if (!lightRef.current) return
    const { ambient, directional } = lightRef.current

    if (useFallbackLighting) {
      ambient.color = new THREE.Color(preset.color)
      directional.color = new THREE.Color(preset.color)
      ambient.intensity = preset.ambient
      directional.intensity = preset.directional
    } else {
      ambient.color = new THREE.Color('#ffffff')
      directional.color = new THREE.Color('#ffffff')
      ambient.intensity = 0.08
      directional.intensity = 0.2
    }

    if (rendererRef.current) {
      rendererRef.current.toneMappingExposure = useFallbackLighting ? preset.exposure : 0.82
    }
  }, [preset.ambient, preset.color, preset.directional, preset.exposure, useFallbackLighting])

  useEffect(() => {
    const scene = sceneRef.current
    const pmremGenerator = pmremGeneratorRef.current
    if (!scene || !pmremGenerator) return

    ;(scene as THREE.Scene & { environmentIntensity?: number }).environmentIntensity = useFallbackLighting
      ? 1.0
      : 0.35

    const loadToken = environmentLoadTokenRef.current + 1
    environmentLoadTokenRef.current = loadToken
    const loader = new RGBELoader()
    loader.setDataType(THREE.HalfFloatType)
    loader.load(
      preset.hdriUrl,
      (texture: THREE.DataTexture) => {
        if (environmentLoadTokenRef.current !== loadToken) {
          texture.dispose()
          return
        }
        const renderTarget = pmremGenerator.fromEquirectangular(texture)
        texture.dispose()

        if (environmentLoadTokenRef.current !== loadToken) {
          renderTarget.dispose()
          return
        }

        if (environmentRenderTargetRef.current) {
          environmentRenderTargetRef.current.dispose()
        }
        environmentRenderTargetRef.current = renderTarget
        scene.environment = renderTarget.texture
        ;(scene as THREE.Scene & { environmentIntensity?: number }).environmentIntensity = useFallbackLighting
          ? 1.0
          : 0.35
      },
      undefined,
      (error: unknown) => {
        if (environmentLoadTokenRef.current !== loadToken) return
        console.warn('Failed to load HDR environment map', error)
      }
    )
  }, [preset.hdriUrl, useFallbackLighting])

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
      hasUserCameraOverrideRef.current = false
      setUseFallbackLighting(true)
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
      hasLoadedModelRef.current &&
      !alwaysAutoFrameCameraRef.current &&
      hasUserCameraOverrideRef.current
        ? cameraViewRef.current
        : null

    loader.load(
      gltfUrl,
      (gltf: { scene: THREE.Object3D }) => {
        if (loadTokenRef.current !== loadToken) return

        if (modelRef.current) {
          disposeObject3D(modelRef.current)
          scene.remove(modelRef.current)
        }

        const hasEmbeddedLights = sceneContainsLights(gltf.scene)
        if (hasEmbeddedLights) {
          normalizeEmbeddedLightIntensities(gltf.scene)
        }
        setUseFallbackLighting(!hasEmbeddedLights)
        applyTwoSidedRenderingState(
          gltf.scene,
          twoSidedRenderingRef.current,
          originalMaterialSidesRef.current
        )
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
          const preferredCamera = pickPreferredSceneCamera(collectSceneCameras(gltf.scene))
          if (preferredCamera) {
            applyExportedCameraView(preferredCamera, camera, controls, center, size)
          } else {
            frameCameraToBox(camera, controls, box, size, center)
          }
          hasUserCameraOverrideRef.current = false
        }
        hasLoadedModelRef.current = true
        cameraViewRef.current = {
          position: camera.position.clone(),
          target: controls.target.clone()
        }
        resizeRendererRef.current?.()
        rendererRef.current?.render(scene, camera)

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
          {showFullscreenButton && onToggleFullscreen && (
            <button
              className="ghost-btn viewer-fullscreen-btn"
              onClick={onToggleFullscreen}
              title={isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}
              aria-label={isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}
            >
              {isFullscreen ? (
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="4 14 4 10 0 10" /><polyline points="12 2 12 6 16 6" />
                  <line x1="0" y1="16" x2="6" y2="10" /><line x1="16" y1="0" x2="10" y2="6" />
                </svg>
              ) : (
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="10 0 10 4 14 4" /><polyline points="6 16 6 12 2 12" />
                  <line x1="16" y1="0" x2="10" y2="6" /><line x1="0" y1="16" x2="6" y2="10" />
                </svg>
              )}
            </button>
          )}
          {headerTrailingControls}
        </div>
      </div>
      <div className={`viewer viewport-${resolvedViewportTheme}`}>
        {!gltfUrl && <div className="viewer-placeholder">No model loaded</div>}
        <div className="viewer-canvas" ref={containerRef} />
      </div>
    </div>
  )
}
