import { useEffect, useEffectEvent, useMemo, useRef, useState, type ReactNode } from 'react'
// @ts-expect-error project does not include three type declarations in this workspace.
import * as THREE from 'three'
// @ts-expect-error project does not include three example type declarations in this workspace.
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader'
// @ts-expect-error project does not include three example type declarations in this workspace.
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls'
// @ts-expect-error project does not include three example type declarations in this workspace.
import { TransformControls } from 'three/examples/jsm/controls/TransformControls'
// @ts-expect-error project does not include three example type declarations in this workspace.
import { RGBELoader } from 'three/examples/jsm/loaders/RGBELoader'
// @ts-expect-error project does not include three example type declarations in this workspace.
import { Sky } from 'three/examples/jsm/objects/Sky'
// @ts-expect-error project does not include three example type declarations in this workspace.
import { Wireframe } from 'three/examples/jsm/lines/Wireframe'
// @ts-expect-error project does not include three example type declarations in this workspace.
import { WireframeGeometry2 } from 'three/examples/jsm/lines/WireframeGeometry2'
// @ts-expect-error project does not include three example type declarations in this workspace.
import { LineMaterial } from 'three/examples/jsm/lines/LineMaterial'
import {
  environmentPresets,
  type EnvironmentPreset,
  type EnvironmentPresetConfig,
  type ProceduralSkyVisualConfig
} from '../constants/environmentPresets'
import type {
  SceneHierarchyNode,
  SceneObjectTransformMode,
  SceneObjectTransformUpdate
} from '../state/types'

type ViewportTheme = 'auto' | 'dark' | 'light'
type UiTheme = 'dark' | 'light'
type ShadingMode = 'lit' | 'unlit' | 'wireframe'
type ProceduralSkyConfig = NonNullable<EnvironmentPresetConfig['proceduralSky']>
const PROCEDURAL_SKY_RADIUS = 450
const WIREFRAME_OVERLAY_LINEWIDTH = 1.85
const CLICK_SELECTION_TOLERANCE_PX = 8
type ViewportPalette = {
  background: number
  defaultGridMajor: number
  defaultGridMinor: number
  modelGridMajor: number
  modelGridMinor: number
  wireframe: number
  selectionBox: number
  selectionFill: number
}

type TransformUndoEntry = {
  backendObjectId: string
  position: [number, number, number]
  quaternion: [number, number, number, number]
  scale: [number, number, number]
}

type GltfViewerProps = {
  gltfUrl: string | null
  environment: EnvironmentPreset
  environmentLightIntensity?: number
  environmentBackgroundIntensity?: number
  viewportTheme?: ViewportTheme
  uiTheme?: UiTheme
  showGrid?: boolean
  showHdriBackground?: boolean
  twoSidedRendering?: boolean
  showWireframeOverlay?: boolean
  shadingMode?: ShadingMode
  selectedBackendObjectId?: string | null
  transformEnabled?: boolean
  canRequestTransform?: boolean
  transformMode?: SceneObjectTransformMode
  onHierarchyChange?: (nodes: SceneHierarchyNode[]) => void
  onSelectBackendObject?: (backendObjectId: string | null) => void
  hiddenBackendObjectIds?: string[]
  onTransformModeChange?: (mode: SceneObjectTransformMode) => void
  onTransformObject?: (transform: SceneObjectTransformUpdate) => Promise<boolean>
  onTransformDragChange?: (dragging: boolean) => void
  onAddToChat?: (backendObjectId: string, backendObjectName: string | null) => void
  onRequestDeleteSelected?: (backendObjectId: string) => void
  undoRef?: React.MutableRefObject<(() => boolean) | null>
  onUndoCountChange?: (count: number) => void
  isFullscreen?: boolean
  onToggleFullscreen?: () => void
  showFullscreenButton?: boolean
  headerControls?: ReactNode
  headerTrailingControls?: ReactNode
  footerControls?: ReactNode
  alwaysAutoFrameCamera?: boolean
  focusViewportRequest?: number
}

const viewportPalettes: Record<UiTheme, ViewportPalette> = {
  dark: {
    background: 0x0f1117,
    defaultGridMajor: 0x36425a,
    defaultGridMinor: 0x1e2534,
    modelGridMajor: 0x3a4865,
    modelGridMinor: 0x1e2534,
    wireframe: 0xffffff,
    selectionBox: 0x7ed0ff,
    selectionFill: 0x5fa4ff
  },
  light: {
    background: 0xf3f6fb,
    defaultGridMajor: 0xb4c3d8,
    defaultGridMinor: 0xd6dfeb,
    modelGridMajor: 0xa8bad2,
    modelGridMinor: 0xd6dfeb,
    wireframe: 0x101820,
    selectionBox: 0x216ad4,
    selectionFill: 0x4d7be8
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

function focusCameraOnObject(
  camera: THREE.PerspectiveCamera,
  controls: OrbitControls,
  object: THREE.Object3D
): boolean {
  object.updateWorldMatrix(true, true)
  const box = new THREE.Box3().setFromObject(object)
  if (box.isEmpty()) {
    return false
  }

  const size = box.getSize(new THREE.Vector3())
  const center = box.getCenter(new THREE.Vector3())
  const sphere = box.getBoundingSphere(new THREE.Sphere())
  const radius = Math.max(sphere.radius, 0.1)
  const verticalFov = (camera.fov * Math.PI) / 180
  const horizontalFov = 2 * Math.atan(Math.tan(verticalFov / 2) * Math.max(camera.aspect, 0.1))
  const limitingFov = Math.max(Math.PI / 18, Math.min(verticalFov, horizontalFov))
  const distance = (radius / Math.sin(limitingFov / 2)) * 1.18

  const viewDirection = camera.position.clone().sub(controls.target)
  const fallbackDirection = getAutoFrameDirection(size)
  const direction =
    viewDirection.lengthSq() > 1e-6 ? viewDirection.normalize() : fallbackDirection
  const focus = center.clone()
  if (size.y > 0) {
    focus.y = box.min.y + size.y * 0.45
  }

  camera.position.copy(focus).addScaledVector(direction, distance)
  camera.near = Math.max(distance / 1000, radius / 200, 0.01)
  camera.far = Math.max(distance + radius * 10, 1000)
  camera.updateProjectionMatrix()
  controls.target.copy(focus)
  controls.update()
  return true
}

function buildHierarchy(object: THREE.Object3D): SceneHierarchyNode {
  const backendObjectId =
    typeof object.userData?.scene_agent_object_id === 'string' && object.userData.scene_agent_object_id.trim()
      ? object.userData.scene_agent_object_id.trim()
      : null
  const backendObjectName =
    typeof object.userData?.scene_agent_object_name === 'string' && object.userData.scene_agent_object_name.trim()
      ? object.userData.scene_agent_object_name.trim()
      : null
  return {
    nodeId: object.uuid,
    name: object.name.trim() || object.type,
    type: object.type,
    children: object.children.map((child: THREE.Object3D) => buildHierarchy(child)),
    backendObjectId,
    backendObjectName,
    deletable: Boolean(backendObjectId),
    transformable: Boolean(backendObjectId),
    referencable: Boolean(backendObjectId)
  }
}

function buildBackendObjectLookup(object: THREE.Object3D): Map<string, THREE.Object3D> {
  const lookup = new Map<string, THREE.Object3D>()
  object.traverse((entry: THREE.Object3D) => {
    const backendObjectId =
      typeof entry.userData?.scene_agent_object_id === 'string' && entry.userData.scene_agent_object_id.trim()
        ? entry.userData.scene_agent_object_id.trim()
        : null
    if (backendObjectId && !lookup.has(backendObjectId)) {
      lookup.set(backendObjectId, entry)
    }
  })
  return lookup
}

function getBackendObjectId(object: THREE.Object3D | null | undefined): string | null {
  if (!object) return null
  const candidate = object.userData?.scene_agent_object_id
  if (typeof candidate !== 'string') {
    return null
  }
  const normalized = candidate.trim()
  return normalized || null
}

function getBackendObjectName(object: THREE.Object3D | null | undefined): string | null {
  if (!object) return null
  const candidate = object.userData?.scene_agent_object_name
  if (typeof candidate === 'string' && candidate.trim()) {
    return candidate.trim()
  }
  const fallback = object.name.trim()
  return fallback || null
}

function resolveBackendSelectableObject(
  object: THREE.Object3D | null,
  model: THREE.Object3D | null
): THREE.Object3D | null {
  if (!object || !model) {
    return null
  }

  let current: THREE.Object3D | null = object
  while (current) {
    if (getBackendObjectId(current)) {
      return current
    }
    if (current === model) {
      break
    }
    current = current.parent
  }

  return null
}

function serializeWorldMatrix(matrix: THREE.Matrix4): number[][] {
  const elements = matrix.elements
  return [
    [elements[0], elements[4], elements[8], elements[12]],
    [elements[1], elements[5], elements[9], elements[13]],
    [elements[2], elements[6], elements[10], elements[14]],
    [elements[3], elements[7], elements[11], elements[15]]
  ].map((row) => row.map((value) => Number(value.toFixed(6))))
}

function computeTransformGizmoSize(target: THREE.Object3D, camera: THREE.PerspectiveCamera): number {
  const box = new THREE.Box3().setFromObject(target)
  if (box.isEmpty()) {
    return 0.5
  }
  const size = new THREE.Vector3()
  const center = new THREE.Vector3()
  box.getSize(size)
  box.getCenter(center)
  const maxDimension = Math.max(size.x, size.y, size.z, 0.001)
  const distance = Math.max(camera.position.distanceTo(center), 0.001)
  const ratio = maxDimension / distance
  return THREE.MathUtils.clamp(0.35 + ratio * 1.5, 0.3, 1.0)
}

function suppressNegativeAxisHandles(helper: THREE.Object3D | null): void {
  if (!helper) return
  const center = new THREE.Vector3()
  const toRemove: THREE.Object3D[] = []

  helper.traverse((child: THREE.Object3D) => {
    const mesh = child as THREE.Mesh & { isMesh?: boolean; geometry?: THREE.BufferGeometry }
    if (!mesh.isMesh || !mesh.geometry?.attributes?.position) return
    const name = mesh.name
    if (name !== 'X' && name !== 'Y' && name !== 'Z') return

    const bbox = new THREE.Box3()
    bbox.setFromBufferAttribute(mesh.geometry.attributes.position)
    bbox.getCenter(center)

    if (
      (name === 'X' && center.x < -0.1) ||
      (name === 'Y' && center.y < -0.1) ||
      (name === 'Z' && center.z < -0.1)
    ) {
      toRemove.push(mesh)
    }
  })

  for (const obj of toRemove) {
    obj.parent?.remove(obj)
    const m = obj as THREE.Mesh
    m.geometry?.dispose()
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

function disposeEphemeralMaterial(material: THREE.Material | THREE.Material[]): void {
  if (Array.isArray(material)) {
    material.forEach((entry) => disposeEphemeralMaterial(entry))
    return
  }
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

function createUnlitMaterial(sourceMaterial: THREE.Material): THREE.MeshBasicMaterial {
  const source = sourceMaterial as THREE.Material & {
    alphaMap?: THREE.Texture | null
    alphaTest?: number
    color?: THREE.Color
    map?: THREE.Texture | null
    opacity?: number
    side?: THREE.Side
    transparent?: boolean
    vertexColors?: boolean
    wireframe?: boolean
    depthTest?: boolean
    depthWrite?: boolean
    name?: string
  }
  const material = new THREE.MeshBasicMaterial({
    color: source.color?.clone() ?? new THREE.Color(0xffffff),
    map: source.map ?? null,
    opacity: typeof source.opacity === 'number' ? source.opacity : 1,
    transparent: Boolean(source.transparent),
    alphaMap: source.alphaMap ?? null,
    alphaTest: typeof source.alphaTest === 'number' ? source.alphaTest : 0,
    side: source.side ?? THREE.FrontSide,
    vertexColors: Boolean(source.vertexColors),
    wireframe: Boolean(source.wireframe)
  })
  material.name = source.name ? `${source.name}__scene_agent_unlit` : 'scene_agent_unlit'
  material.depthTest = source.depthTest ?? true
  material.depthWrite = source.depthWrite ?? true
  material.toneMapped = false
  return material
}

function applyShadingModeToModel(
  object: THREE.Object3D | null,
  shadingMode: ShadingMode,
  originalMaterials: WeakMap<THREE.Object3D, THREE.Material | THREE.Material[]>,
  unlitMaterials: WeakMap<THREE.Object3D, THREE.Material | THREE.Material[]>
): void {
  if (!object) return

  object.traverse((entry: THREE.Object3D) => {
    const mesh = entry as THREE.Mesh & {
      isMesh?: boolean
      material?: THREE.Material | THREE.Material[]
    }
    if (!mesh.isMesh || !mesh.material) {
      return
    }

    if (shadingMode === 'unlit') {
      if (!originalMaterials.has(mesh)) {
        originalMaterials.set(mesh, mesh.material)
      }
      const originalMaterial = originalMaterials.get(mesh)
      if (!originalMaterial) {
        return
      }
      const replacement = Array.isArray(originalMaterial)
        ? originalMaterial.map((material) => createUnlitMaterial(material))
        : createUnlitMaterial(originalMaterial)
      const previousReplacement = unlitMaterials.get(mesh)
      if (previousReplacement) {
        disposeEphemeralMaterial(previousReplacement)
      }
      mesh.material = replacement
      unlitMaterials.set(mesh, replacement)
      return
    }

    const originalMaterial = originalMaterials.get(mesh)
    if (!originalMaterial) {
      return
    }
    const replacement = unlitMaterials.get(mesh)
    mesh.material = originalMaterial
    if (replacement) {
      disposeEphemeralMaterial(replacement)
      unlitMaterials.delete(mesh)
    }
    originalMaterials.delete(mesh)
  })
}

function restoreOriginalMaterials(
  object: THREE.Object3D | null,
  originalMaterials: WeakMap<THREE.Object3D, THREE.Material | THREE.Material[]>,
  unlitMaterials: WeakMap<THREE.Object3D, THREE.Material | THREE.Material[]>
): void {
  applyShadingModeToModel(object, 'lit', originalMaterials, unlitMaterials)
}

function getProceduralSunPosition(config: ProceduralSkyVisualConfig): THREE.Vector3 {
  const phi = THREE.MathUtils.degToRad(90 - config.elevation)
  const theta = THREE.MathUtils.degToRad(config.azimuth)
  return new THREE.Vector3().setFromSphericalCoords(1, phi, theta).normalize()
}

function resolveProceduralSkyVisualConfig(
  config: ProceduralSkyConfig,
  variant: 'environment' | 'background'
): ProceduralSkyVisualConfig {
  if (variant === 'background' && config.background) {
    return {
      turbidity: config.background.turbidity ?? config.turbidity,
      rayleigh: config.background.rayleigh ?? config.rayleigh,
      mieCoefficient: config.background.mieCoefficient ?? config.mieCoefficient,
      mieDirectionalG: config.background.mieDirectionalG ?? config.mieDirectionalG,
      elevation: config.background.elevation ?? config.elevation,
      azimuth: config.background.azimuth ?? config.azimuth
    }
  }
  return {
    turbidity: config.turbidity,
    rayleigh: config.rayleigh,
    mieCoefficient: config.mieCoefficient,
    mieDirectionalG: config.mieDirectionalG,
    elevation: config.elevation,
    azimuth: config.azimuth
  }
}

function applyProceduralSkyUniforms(sky: Sky, config: ProceduralSkyVisualConfig): void {
  const material = sky.material as THREE.ShaderMaterial & {
    uniforms: {
      turbidity: { value: number }
      rayleigh: { value: number }
      mieCoefficient: { value: number }
      mieDirectionalG: { value: number }
      sunPosition: { value: THREE.Vector3 }
    }
  }
  const sunPosition = getProceduralSunPosition(config)

  material.uniforms.turbidity.value = config.turbidity
  material.uniforms.rayleigh.value = config.rayleigh
  material.uniforms.mieCoefficient.value = config.mieCoefficient
  material.uniforms.mieDirectionalG.value = config.mieDirectionalG
  material.uniforms.sunPosition.value.copy(sunPosition)
}

function createProceduralSky(config: ProceduralSkyVisualConfig): Sky {
  const sky = new Sky()
  sky.scale.setScalar(PROCEDURAL_SKY_RADIUS)
  applyProceduralSkyUniforms(sky, config)
  return sky
}

function applyProceduralSkyBackgroundIntensity(sky: Sky, intensity: number): void {
  const material = sky.material as THREE.ShaderMaterial & {
    uniforms: Record<string, { value: unknown }>
    userData: { backgroundIntensityPatched?: boolean }
  }

  if (!material.userData.backgroundIntensityPatched) {
    material.uniforms.backgroundIntensity = { value: intensity }
    material.fragmentShader = material.fragmentShader
      .replace('uniform vec3 up;\n', 'uniform vec3 up;\nuniform float backgroundIntensity;\n')
      .replace(
        'gl_FragColor = vec4( retColor, 1.0 );',
        'gl_FragColor = vec4( retColor * backgroundIntensity, 1.0 );'
      )
    material.userData.backgroundIntensityPatched = true
    material.needsUpdate = true
    return
  }

  material.uniforms.backgroundIntensity.value = intensity
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

function buildViewportGrid(
  model: THREE.Object3D | null,
  palette: ViewportPalette
): THREE.GridHelper {
  const configureGrid = (
    grid: THREE.GridHelper,
    majorOpacity: number,
    minorOpacity: number
  ): THREE.GridHelper => {
    const materials = Array.isArray(grid.material) ? grid.material : [grid.material]
    const [majorMaterial, minorMaterial] = materials as Array<
      THREE.Material & { transparent?: boolean; opacity?: number; depthWrite?: boolean }
    >
    if (majorMaterial) {
      majorMaterial.transparent = true
      majorMaterial.opacity = majorOpacity
      majorMaterial.depthWrite = false
    }
    if (minorMaterial) {
      minorMaterial.transparent = true
      minorMaterial.opacity = minorOpacity
      minorMaterial.depthWrite = false
    }
    return grid
  }

  if (model) {
    const box = new THREE.Box3().setFromObject(model)
    const size = new THREE.Vector3()
    box.getSize(size)
    const maxDim = Math.max(size.x, size.y, size.z, 0.1)
    const gridSize = Math.max(200, Math.ceil((maxDim * 20) / 20) * 20)
    const divisions = Math.max(60, Math.min(320, gridSize))
    const grid = configureGrid(new THREE.GridHelper(
      gridSize,
      divisions,
      palette.modelGridMajor,
      palette.modelGridMinor
    ), 0.52, 0.18)
    grid.position.y = box.min.y - 0.001
    return grid
  }

  const grid = configureGrid(new THREE.GridHelper(
    20,
    40,
    palette.defaultGridMajor,
    palette.defaultGridMinor
  ), 0.46, 0.16)
  grid.position.y = -0.01
  return grid
}

function replaceViewportGrid(
  scene: THREE.Scene,
  gridRef: { current: THREE.GridHelper | null },
  nextGrid: THREE.GridHelper | null
): void {
  if (gridRef.current) {
    disposeObject3D(gridRef.current)
    scene.remove(gridRef.current)
  }
  if (nextGrid) {
    scene.add(nextGrid)
  }
  gridRef.current = nextGrid
}

function disposeWireframeOverlay(object: THREE.Object3D | null): void {
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

function buildWireframeOverlay(
  model: THREE.Object3D,
  color: number
): THREE.Group {
  const overlay = new THREE.Group()
  model.updateWorldMatrix(true, true)

  model.traverse((entry: THREE.Object3D) => {
    const mesh = entry as THREE.Mesh & {
      isMesh?: boolean
      geometry?: THREE.BufferGeometry
    }
    if (!mesh.isMesh || !mesh.geometry) return

    const wireframeGeometry = new WireframeGeometry2(mesh.geometry)
    const wireframeMaterial = new LineMaterial({
      color,
      linewidth: WIREFRAME_OVERLAY_LINEWIDTH,
      transparent: true,
      opacity: 0.95,
      depthTest: true,
      depthWrite: false
    })
    wireframeMaterial.toneMapped = false
    const wireframe = new Wireframe(wireframeGeometry, wireframeMaterial)
    wireframe.matrixAutoUpdate = false
    wireframe.matrix.copy(mesh.matrixWorld)
    wireframe.frustumCulled = false
    wireframe.renderOrder = 10
    overlay.add(wireframe)
  })

  return overlay
}

function replaceWireframeOverlay(
  scene: THREE.Scene,
  overlayRef: { current: THREE.Group | null },
  nextOverlay: THREE.Group | null
): void {
  if (overlayRef.current) {
    scene.remove(overlayRef.current)
    disposeWireframeOverlay(overlayRef.current)
  }
  if (nextOverlay) {
    scene.add(nextOverlay)
  }
  overlayRef.current = nextOverlay
}

function formatDebugVector(vector: THREE.Vector3 | null | undefined): [number, number, number] | null {
  if (!vector) return null
  return [vector.x, vector.y, vector.z].map((value) => Number(value.toFixed(3))) as [number, number, number]
}

function buildSelectionOverlay(
  target: THREE.Object3D,
  fillColor: number
): THREE.Group | null {
  const overlay = new THREE.Group()
  target.updateWorldMatrix(true, true)

  target.traverse((entry: THREE.Object3D) => {
    const mesh = entry as THREE.Mesh & {
      isMesh?: boolean
      geometry?: THREE.BufferGeometry
    }
    if (!mesh.isMesh || !mesh.geometry) {
      return
    }

    const highlight = new THREE.Mesh(
      mesh.geometry.clone(),
      new THREE.MeshBasicMaterial({
        color: fillColor,
        transparent: true,
        opacity: 0.16,
        depthTest: false,
        depthWrite: false,
        side: THREE.DoubleSide
      })
    )
    highlight.matrixAutoUpdate = false
    highlight.matrix.copy(mesh.matrixWorld)
    highlight.frustumCulled = false
    highlight.renderOrder = 18
    overlay.add(highlight)
  })

  return overlay.children.length > 0 ? overlay : null
}

function replaceSelectionOverlay(
  scene: THREE.Scene,
  overlayRef: { current: THREE.Group | null },
  nextOverlay: THREE.Group | null
): void {
  if (overlayRef.current) {
    scene.remove(overlayRef.current)
    disposeObject3D(overlayRef.current)
  }
  if (nextOverlay) {
    scene.add(nextOverlay)
  }
  overlayRef.current = nextOverlay
}

export function GltfViewer({
  gltfUrl,
  environment,
  environmentLightIntensity = 1,
  environmentBackgroundIntensity = 0.78,
  viewportTheme = 'auto',
  uiTheme = 'dark',
  showGrid = true,
  showHdriBackground = false,
  twoSidedRendering = false,
  showWireframeOverlay = false,
  shadingMode = 'lit',
  selectedBackendObjectId = null,
  transformEnabled = false,
  canRequestTransform = false,
  transformMode = 'translate',
  onHierarchyChange,
  onSelectBackendObject,
  hiddenBackendObjectIds = [],
  onTransformModeChange,
  onTransformObject,
  onTransformDragChange,
  onAddToChat,
  onRequestDeleteSelected,
  undoRef,
  onUndoCountChange,
  isFullscreen = false,
  onToggleFullscreen,
  showFullscreenButton = true,
  headerControls,
  headerTrailingControls,
  footerControls,
  alwaysAutoFrameCamera = false,
  focusViewportRequest = 0
}: GltfViewerProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const sceneRef = useRef<THREE.Scene | null>(null)
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null)
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null)
  const controlsRef = useRef<OrbitControls | null>(null)
  const transformControlsRef = useRef<TransformControls | null>(null)
  const transformControlsHelperRef = useRef<THREE.Object3D | null>(null)
  const modelRef = useRef<THREE.Object3D | null>(null)
  const backendObjectLookupRef = useRef<Map<string, THREE.Object3D>>(new Map())
  const backendObjectVisibilityRef = useRef<WeakMap<THREE.Object3D, boolean>>(new WeakMap())
  const gridRef = useRef<THREE.GridHelper | null>(null)
  const wireframeOverlayRef = useRef<THREE.Group | null>(null)
  const selectionOverlayRef = useRef<THREE.Group | null>(null)
  const lightRef = useRef<{
    ambient: THREE.AmbientLight
    hemisphere: THREE.HemisphereLight
    directional: THREE.DirectionalLight
  } | null>(null)
  const pmremGeneratorRef = useRef<THREE.PMREMGenerator | null>(null)
  const environmentRenderTargetRef = useRef<THREE.WebGLRenderTarget | null>(null)
  const environmentBackgroundTextureRef = useRef<THREE.DataTexture | null>(null)
  const environmentBackdropRef = useRef<THREE.Object3D | null>(null)
  const environmentLoadTokenRef = useRef(0)
  const loadTokenRef = useRef(0)
  const hasLoadedModelRef = useRef(false)
  const cameraViewRef = useRef<{ position: THREE.Vector3; target: THREE.Vector3 } | null>(null)
  const hasUserCameraOverrideRef = useRef(false)
  const resizeRendererRef = useRef<(() => void) | null>(null)
  const originalMaterialSidesRef = useRef<WeakMap<THREE.Material, number>>(new WeakMap())
  const originalMeshMaterialsRef = useRef<WeakMap<THREE.Object3D, THREE.Material | THREE.Material[]>>(new WeakMap())
  const unlitMeshMaterialsRef = useRef<WeakMap<THREE.Object3D, THREE.Material | THREE.Material[]>>(new WeakMap())
  const twoSidedRenderingRef = useRef(twoSidedRendering)
  const shadingModeRef = useRef<ShadingMode>(shadingMode)
  const selectedBackendObjectIdRef = useRef<string | null>(selectedBackendObjectId)
  const focusViewportRequestRef = useRef(focusViewportRequest)
  const appliedFocusViewportRequestRef = useRef(focusViewportRequest)
  const transformEnabledRef = useRef(transformEnabled)
  const canRequestTransformRef = useRef(canRequestTransform)
  const transformModeRef = useRef<SceneObjectTransformMode>(transformMode)
  const onSelectBackendObjectRef = useRef(onSelectBackendObject)
  const onTransformModeChangeRef = useRef(onTransformModeChange)
  const onTransformObjectRef = useRef(onTransformObject)
  const onTransformDragChangeRef = useRef(onTransformDragChange)
  const transformSyncTimerRef = useRef<number | null>(null)
  const pendingTransformUpdateRef = useRef<SceneObjectTransformUpdate | null>(null)
  const transformSyncInFlightRef = useRef(false)
  const dragPointerDownRef = useRef<{ x: number; y: number } | null>(null)
  const draggingTransformRef = useRef(false)
  const suppressSelectionUntilRef = useRef(0)
  const undoStackRef = useRef<TransformUndoEntry[]>([])
  const onUndoCountChangeRef = useRef(onUndoCountChange)
  const onAddToChatRef = useRef(onAddToChat)
  const onRequestDeleteSelectedRef = useRef(onRequestDeleteSelected)
  const lastQPressRef = useRef(0)
  const [useFallbackLighting, setUseFallbackLighting] = useState(true)
  const [viewerResetToken, setViewerResetToken] = useState(0)
  const debugCameraReload = useEffectEvent(
    (
      phase: 'load-start' | 'restore-preserved-view',
      details: {
        hasLoadedModel: boolean
        hasUserCameraOverride: boolean
        usedPreservedView: boolean
        cameraPosition?: THREE.Vector3 | null
        cameraTarget?: THREE.Vector3 | null
      }
    ) => {
      if (!import.meta.env.DEV) return
      console.debug('[GltfViewer] camera reload', {
        phase,
        gltfUrl,
        alwaysAutoFrameCamera,
        hasLoadedModel: details.hasLoadedModel,
        hasUserCameraOverride: details.hasUserCameraOverride,
        usedPreservedView: details.usedPreservedView,
        cameraPosition: formatDebugVector(details.cameraPosition),
        cameraTarget: formatDebugVector(details.cameraTarget)
      })
    }
  )

  const preset = useMemo(() => environmentPresets[environment], [environment])
  const resolvedEnvironmentLightIntensity = useMemo(
    () => Math.min(1.35, Math.max(0.35, environmentLightIntensity)),
    [environmentLightIntensity]
  )
  const resolvedEnvironmentBackgroundIntensity = useMemo(
    () => Math.min(1.35, Math.max(0.15, environmentBackgroundIntensity)),
    [environmentBackgroundIntensity]
  )
  const resolvedViewportTheme: UiTheme =
    viewportTheme === 'auto' ? uiTheme : viewportTheme
  const viewportPalette = useMemo(
    () => viewportPalettes[resolvedViewportTheme],
    [resolvedViewportTheme]
  )
  const viewportPaletteRef = useRef(viewportPalette)
  const onHierarchyChangeRef = useRef(onHierarchyChange)
  const alwaysAutoFrameCameraRef = useRef(alwaysAutoFrameCamera)
  const showGridRef = useRef(showGrid)
  const showWireframeOverlayRef = useRef(showWireframeOverlay)

  const refreshSelectionOverlay = useEffectEvent(() => {
    const scene = sceneRef.current
    const model = modelRef.current
    if (!scene || !model || !selectedBackendObjectIdRef.current) {
      if (scene) {
        replaceSelectionOverlay(scene, selectionOverlayRef, null)
      }
      return
    }

    const selectedObject = backendObjectLookupRef.current.get(selectedBackendObjectIdRef.current) ?? null
    const nextOverlay = selectedObject
      ? buildSelectionOverlay(
          selectedObject,
          viewportPaletteRef.current.selectionFill
        )
      : null
    replaceSelectionOverlay(scene, selectionOverlayRef, nextOverlay)
  })

  const focusSelectedObject = useEffectEvent(() => {
    const camera = cameraRef.current
    const controls = controlsRef.current
    const scene = sceneRef.current
    const backendObjectId = selectedBackendObjectIdRef.current
    if (!camera || !controls || !scene || !backendObjectId) {
      return false
    }

    const selectedObject = backendObjectLookupRef.current.get(backendObjectId) ?? null
    if (!selectedObject || !focusCameraOnObject(camera, controls, selectedObject)) {
      return false
    }

    hasUserCameraOverrideRef.current = true
    cameraViewRef.current = {
      position: camera.position.clone(),
      target: controls.target.clone()
    }
    appliedFocusViewportRequestRef.current = focusViewportRequestRef.current
    rendererRef.current?.render(scene, camera)
    return true
  })

  const refreshTransformControls = useEffectEvent(() => {
    const transformControls = transformControlsRef.current
    const transformControlsHelper = transformControlsHelperRef.current
    const camera = cameraRef.current
    const model = modelRef.current
    if (!transformControls || !camera) {
      return
    }

    const activeMode = transformModeRef.current
    const isSelectOnly = activeMode === 'select'
    const threeMode = isSelectOnly ? 'translate' : activeMode
    transformControls.setMode(threeMode)
    transformControls.enabled = transformEnabledRef.current && !isSelectOnly

    if (!transformEnabledRef.current || isSelectOnly || !model || !selectedBackendObjectIdRef.current) {
      transformControls.detach()
      transformControls.visible = false
      if (transformControlsHelper) {
        transformControlsHelper.visible = false
      }
      return
    }

    const targetObject =
      backendObjectLookupRef.current.get(selectedBackendObjectIdRef.current) ?? null
    if (!targetObject) {
      transformControls.detach()
      transformControls.visible = false
      if (transformControlsHelper) {
        transformControlsHelper.visible = false
      }
      return
    }

    transformControls.visible = true
    if (transformControlsHelper) {
      transformControlsHelper.visible = true
    }
    transformControls.attach(targetObject)
    transformControls.size = computeTransformGizmoSize(targetObject, camera)
    suppressNegativeAxisHandles(transformControlsHelper)
  })

  const collectTransformUpdate = useEffectEvent(
    (commit: boolean): SceneObjectTransformUpdate | null => {
      const model = modelRef.current
      const backendObjectId = selectedBackendObjectIdRef.current
      if (!model || !backendObjectId) {
        return null
      }
      const targetObject = backendObjectLookupRef.current.get(backendObjectId) ?? null
      if (!targetObject) {
        return null
      }
      targetObject.updateWorldMatrix(true, true)
      return {
        backendObjectId,
        backendObjectName: getBackendObjectName(targetObject),
        worldMatrix: serializeWorldMatrix(targetObject.matrixWorld),
        commit
      }
    }
  )

  const flushPendingTransformUpdate = useEffectEvent(function flushPendingTransformUpdateImpl() {
    if (transformSyncTimerRef.current !== null) {
      window.clearTimeout(transformSyncTimerRef.current)
      transformSyncTimerRef.current = null
    }
    if (transformSyncInFlightRef.current) {
      return
    }
    const pending = pendingTransformUpdateRef.current
    const handler = onTransformObjectRef.current
    if (!pending || !handler) {
      pendingTransformUpdateRef.current = null
      return
    }

    pendingTransformUpdateRef.current = null
    transformSyncInFlightRef.current = true
    void handler(pending)
      .catch((error) => {
        console.error('Failed to sync object transform', error)
      })
      .finally(() => {
        transformSyncInFlightRef.current = false
        if (pendingTransformUpdateRef.current) {
          if (pendingTransformUpdateRef.current.commit) {
            flushPendingTransformUpdateImpl()
            return
          }
          transformSyncTimerRef.current = window.setTimeout(() => {
            transformSyncTimerRef.current = null
            flushPendingTransformUpdateImpl()
          }, 220)
        }
      })
  })

  const enqueueTransformUpdate = useEffectEvent((commit: boolean) => {
    const update = collectTransformUpdate(commit)
    if (!update) {
      return
    }
    if (pendingTransformUpdateRef.current) {
      pendingTransformUpdateRef.current = {
        ...update,
        commit: pendingTransformUpdateRef.current.commit || update.commit
      }
    } else {
      pendingTransformUpdateRef.current = update
    }

    if (commit) {
      flushPendingTransformUpdate()
      return
    }

    if (transformSyncTimerRef.current !== null || transformSyncInFlightRef.current) {
      return
    }
    transformSyncTimerRef.current = window.setTimeout(() => {
      transformSyncTimerRef.current = null
      flushPendingTransformUpdate()
    }, 220)
  })

  const captureUndoSnapshot = useEffectEvent((): TransformUndoEntry | null => {
    const backendObjectId = selectedBackendObjectIdRef.current
    if (!backendObjectId) return null
    const target = backendObjectLookupRef.current.get(backendObjectId) ?? null
    if (!target) return null
    return {
      backendObjectId,
      position: [target.position.x, target.position.y, target.position.z],
      quaternion: [target.quaternion.x, target.quaternion.y, target.quaternion.z, target.quaternion.w],
      scale: [target.scale.x, target.scale.y, target.scale.z]
    }
  })

  const pushUndoSnapshot = useEffectEvent((entry: TransformUndoEntry) => {
    undoStackRef.current = [...undoStackRef.current.slice(-49), entry]
    onUndoCountChangeRef.current?.(undoStackRef.current.length)
  })

  const performUndo = useEffectEvent((): boolean => {
    const stack = undoStackRef.current
    if (stack.length === 0) return false
    const entry = stack[stack.length - 1]
    undoStackRef.current = stack.slice(0, -1)
    onUndoCountChangeRef.current?.(undoStackRef.current.length)
    const target = backendObjectLookupRef.current.get(entry.backendObjectId) ?? null
    if (!target) return false
    target.position.set(...entry.position)
    target.quaternion.set(...entry.quaternion)
    target.scale.set(...entry.scale)
    target.updateWorldMatrix(true, true)
    if (selectedBackendObjectIdRef.current !== entry.backendObjectId) {
      onSelectBackendObjectRef.current?.(entry.backendObjectId)
    }
    refreshSelectionOverlay()
    refreshTransformControls()
    onTransformDragChangeRef.current?.(true)
    onTransformDragChangeRef.current?.(false)
    enqueueTransformUpdate(true)
    return true
  })

  const applyShadingMode = useEffectEvent(() => {
    const materialMode = shadingModeRef.current === 'wireframe' ? 'lit' : shadingModeRef.current
    applyShadingModeToModel(
      modelRef.current,
      materialMode,
      originalMeshMaterialsRef.current,
      unlitMeshMaterialsRef.current
    )
    applyTwoSidedRenderingState(modelRef.current, twoSidedRenderingRef.current, originalMaterialSidesRef.current)
  })

  useEffect(() => {
    viewportPaletteRef.current = viewportPalette
    refreshSelectionOverlay()
  }, [viewportPalette])

  useEffect(() => {
    onHierarchyChangeRef.current = onHierarchyChange
  }, [onHierarchyChange])

  useEffect(() => {
    onSelectBackendObjectRef.current = onSelectBackendObject
  }, [onSelectBackendObject])

  useEffect(() => {
    onTransformModeChangeRef.current = onTransformModeChange
  }, [onTransformModeChange])

  useEffect(() => {
    onTransformObjectRef.current = onTransformObject
  }, [onTransformObject])

  useEffect(() => {
    onTransformDragChangeRef.current = onTransformDragChange
  }, [onTransformDragChange])

  useEffect(() => {
    onUndoCountChangeRef.current = onUndoCountChange
  }, [onUndoCountChange])

  useEffect(() => {
    onAddToChatRef.current = onAddToChat
  }, [onAddToChat])

  useEffect(() => {
    alwaysAutoFrameCameraRef.current = alwaysAutoFrameCamera
  }, [alwaysAutoFrameCamera])

  useEffect(() => {
    showGridRef.current = showGrid
  }, [showGrid])

  const effectiveWireframe = showWireframeOverlay || shadingMode === 'wireframe'

  useEffect(() => {
    showWireframeOverlayRef.current = effectiveWireframe
  }, [effectiveWireframe])

  useEffect(() => {
    twoSidedRenderingRef.current = twoSidedRendering
    applyTwoSidedRenderingState(modelRef.current, twoSidedRendering, originalMaterialSidesRef.current)
  }, [twoSidedRendering])

  useEffect(() => {
    shadingModeRef.current = shadingMode
    applyShadingMode()
  }, [shadingMode])

  useEffect(() => {
    transformEnabledRef.current = transformEnabled
    refreshTransformControls()
  }, [transformEnabled])

  useEffect(() => {
    canRequestTransformRef.current = canRequestTransform
  }, [canRequestTransform])

  useEffect(() => {
    onRequestDeleteSelectedRef.current = onRequestDeleteSelected
  }, [onRequestDeleteSelected])

  useEffect(() => {
    transformModeRef.current = transformMode
    refreshTransformControls()
  }, [transformMode])

  useEffect(() => {
    selectedBackendObjectIdRef.current = selectedBackendObjectId
    refreshSelectionOverlay()
    refreshTransformControls()
    if (
      appliedFocusViewportRequestRef.current < focusViewportRequestRef.current &&
      selectedBackendObjectId
    ) {
      focusSelectedObject()
    }
  }, [selectedBackendObjectId])

  useEffect(() => {
    const hiddenIds = new Set(hiddenBackendObjectIds)
    for (const [backendObjectId, object] of backendObjectLookupRef.current.entries()) {
      if (!backendObjectVisibilityRef.current.has(object)) {
        backendObjectVisibilityRef.current.set(object, object.visible)
      }
      const originalVisible = backendObjectVisibilityRef.current.get(object)
      object.visible = hiddenIds.has(backendObjectId) ? false : (originalVisible ?? true)
    }
    refreshSelectionOverlay()
    refreshTransformControls()
    const renderer = rendererRef.current
    const scene = sceneRef.current
    const camera = cameraRef.current
    if (renderer && scene && camera) {
      renderer.render(scene, camera)
    }
  }, [gltfUrl, hiddenBackendObjectIds])

  useEffect(() => {
    if (alwaysAutoFrameCamera) {
      hasUserCameraOverrideRef.current = false
    }
  }, [alwaysAutoFrameCamera])

  useEffect(() => {
    focusViewportRequestRef.current = focusViewportRequest
    containerRef.current?.focus({ preventScroll: true })
    focusSelectedObject()
  }, [focusViewportRequest])

  useEffect(() => {
    if (!containerRef.current) return

    const container = containerRef.current
    const originalMeshMaterials = originalMeshMaterialsRef.current
    const unlitMeshMaterials = unlitMeshMaterialsRef.current
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
    const hemisphere = new THREE.HemisphereLight('#dcefff', '#4d5563', 0)
    const directional = new THREE.DirectionalLight('#ffffff', 1.2)
    directional.position.set(8, 14, 6)
    scene.add(ambient, hemisphere, directional)

    const defaultGrid = showGridRef.current ? buildViewportGrid(null, palette) : null
    if (defaultGrid) {
      scene.add(defaultGrid)
    }

    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.08
    controls.target.set(0, 0.5, 0)
    controls.update()
    const transformControls = new TransformControls(camera, renderer.domElement)
    const transformControlsHelper =
      typeof (transformControls as { getHelper?: () => THREE.Object3D }).getHelper === 'function'
        ? (transformControls as { getHelper: () => THREE.Object3D }).getHelper()
        : null
    transformControls.enabled = transformEnabledRef.current
    transformControls.setMode(transformModeRef.current === 'select' ? 'translate' : transformModeRef.current)
    transformControls.visible = false
    if (transformControlsHelper) {
      transformControlsHelper.visible = false
      scene.add(transformControlsHelper)
      suppressNegativeAxisHandles(transformControlsHelper)
    }
    if (undoRef) {
      undoRef.current = performUndo
    }
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
    controls.addEventListener('change', refreshTransformControls)

    const handleTransformDraggingChanged = (event: { value?: boolean }) => {
      const isDragging = Boolean(event?.value)
      draggingTransformRef.current = isDragging
      controls.enabled = !isDragging
      onTransformDragChangeRef.current?.(isDragging)
      if (isDragging) {
        const snapshot = captureUndoSnapshot()
        if (snapshot) {
          pushUndoSnapshot(snapshot)
        }
      }
      if (!isDragging) {
        suppressSelectionUntilRef.current = Date.now() + 160
        enqueueTransformUpdate(true)
      }
    }
    const handleTransformObjectChange = () => {
      refreshSelectionOverlay()
      refreshTransformControls()
      enqueueTransformUpdate(false)
    }
    transformControls.addEventListener('dragging-changed', handleTransformDraggingChanged)
    transformControls.addEventListener('objectChange', handleTransformObjectChange)

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

    let scheduledContextReset = false
    const handleContextLost = (event: Event) => {
      event.preventDefault()
      if (scheduledContextReset) return
      scheduledContextReset = true
      window.setTimeout(() => {
        setViewerResetToken((current) => current + 1)
      }, 0)
    }
    renderer.domElement.addEventListener('webglcontextlost', handleContextLost)

    const resizeObserver = new ResizeObserver(() => {
      resizeRenderer()
    })
    resizeObserver.observe(container)

    sceneRef.current = scene
    rendererRef.current = renderer
    cameraRef.current = camera
    controlsRef.current = controls
    transformControlsRef.current = transformControls
    transformControlsHelperRef.current = transformControlsHelper
    gridRef.current = defaultGrid
    lightRef.current = { ambient, hemisphere, directional }
    pmremGeneratorRef.current = pmremGenerator

    const handlePointerDown = (event: PointerEvent) => {
      if (event.button !== 0) {
        return
      }
      container.focus({ preventScroll: true })
      dragPointerDownRef.current = { x: event.clientX, y: event.clientY }
    }

    const handlePointerUp = (event: PointerEvent) => {
      if (event.button !== 0) {
        return
      }
      const pointerDown = dragPointerDownRef.current
      dragPointerDownRef.current = null
      if (!pointerDown) {
        return
      }
      if (draggingTransformRef.current || Date.now() < suppressSelectionUntilRef.current) {
        return
      }
      const activeAxis = (transformControlsRef.current as { axis?: string | null } | null)?.axis
      if (typeof activeAxis === 'string' && activeAxis.length > 0) {
        return
      }
      const moved =
        Math.abs(event.clientX - pointerDown.x) > CLICK_SELECTION_TOLERANCE_PX ||
        Math.abs(event.clientY - pointerDown.y) > CLICK_SELECTION_TOLERANCE_PX
      if (moved) {
        return
      }

      const model = modelRef.current
      if (!model) {
        onSelectBackendObjectRef.current?.(null)
        return
      }

      const rect = renderer.domElement.getBoundingClientRect()
      const pointer = new THREE.Vector2(
        ((event.clientX - rect.left) / rect.width) * 2 - 1,
        -((event.clientY - rect.top) / rect.height) * 2 + 1
      )
      const raycaster = new THREE.Raycaster()
      raycaster.setFromCamera(pointer, camera)
      const intersections = raycaster.intersectObject(model, true)
      const match =
        intersections
          .map((hit: THREE.Intersection<THREE.Object3D>) =>
            resolveBackendSelectableObject(hit.object, model)
          )
          .find((entry: THREE.Object3D | null): entry is THREE.Object3D => entry !== null) ?? null
      onSelectBackendObjectRef.current?.(getBackendObjectId(match))
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (
        target &&
        (target.closest('input, textarea, select') ||
          target.isContentEditable)
      ) {
        return
      }

      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z' && !event.shiftKey) {
        event.preventDefault()
        performUndo()
        return
      }

      if (event.metaKey || event.ctrlKey || event.altKey) {
        return
      }

      const key = event.key.toLowerCase()
      if (key === 'f' && selectedBackendObjectIdRef.current) {
        const selectedObject =
          backendObjectLookupRef.current.get(selectedBackendObjectIdRef.current) ?? null
        if (selectedObject && focusCameraOnObject(camera, controls, selectedObject)) {
          event.preventDefault()
          hasUserCameraOverrideRef.current = true
        }
        return
      } else if (key === 'x' && selectedBackendObjectIdRef.current) {
        event.preventDefault()
        onRequestDeleteSelectedRef.current?.(selectedBackendObjectIdRef.current)
        return
      }

      if (!canRequestTransformRef.current) {
        return
      }

      if (key === 'q') {
        event.preventDefault()
        const now = Date.now()
        if (now - lastQPressRef.current < 400) {
          onSelectBackendObjectRef.current?.(null)
          lastQPressRef.current = 0
        } else {
          onTransformModeChangeRef.current?.('select')
          lastQPressRef.current = now
        }
      } else if (key === 'w' && selectedBackendObjectIdRef.current) {
        event.preventDefault()
        onTransformModeChangeRef.current?.('translate')
      } else if (key === 'e' && selectedBackendObjectIdRef.current) {
        event.preventDefault()
        onTransformModeChangeRef.current?.('rotate')
      } else if (key === 'r' && selectedBackendObjectIdRef.current) {
        event.preventDefault()
        onTransformModeChangeRef.current?.('scale')
      } else if (key === 'a' && selectedBackendObjectIdRef.current) {
        event.preventDefault()
        const obj = backendObjectLookupRef.current.get(selectedBackendObjectIdRef.current) ?? null
        onAddToChatRef.current?.(
          selectedBackendObjectIdRef.current,
          getBackendObjectName(obj)
        )
      }
    }

    renderer.domElement.addEventListener('pointerdown', handlePointerDown)
    renderer.domElement.addEventListener('pointerup', handlePointerUp)
    window.addEventListener('keydown', handleKeyDown)

    let animationFrame = 0
    const animate = () => {
      if (environmentBackdropRef.current) {
        environmentBackdropRef.current.position.copy(camera.position)
      }
      controls.update()
      renderer.render(scene, camera)
      animationFrame = window.requestAnimationFrame(animate)
    }
    animate()

    return () => {
      window.cancelAnimationFrame(animationFrame)
      resizeObserver.disconnect()
      renderer.domElement.removeEventListener('webglcontextlost', handleContextLost)
      environmentLoadTokenRef.current += 1
      loadTokenRef.current += 1
      controls.removeEventListener('change', syncViewState)
      controls.removeEventListener('start', markUserCameraOverride)
      controls.removeEventListener('change', refreshTransformControls)
      transformControls.removeEventListener('dragging-changed', handleTransformDraggingChanged)
      transformControls.removeEventListener('objectChange', handleTransformObjectChange)
      if (transformSyncTimerRef.current !== null) {
        window.clearTimeout(transformSyncTimerRef.current)
        transformSyncTimerRef.current = null
      }
      pendingTransformUpdateRef.current = null
      transformSyncInFlightRef.current = false
      renderer.domElement.removeEventListener('pointerdown', handlePointerDown)
      renderer.domElement.removeEventListener('pointerup', handlePointerUp)
      window.removeEventListener('keydown', handleKeyDown)
      controls.dispose()
      transformControls.detach()
      if (transformControlsHelperRef.current) {
        scene.remove(transformControlsHelperRef.current)
      }
      if (typeof (transformControls as { dispose?: () => void }).dispose === 'function') {
        ;(transformControls as { dispose: () => void }).dispose()
      }
      if (environmentRenderTargetRef.current) {
        environmentRenderTargetRef.current.dispose()
        environmentRenderTargetRef.current = null
      }
      if (environmentBackgroundTextureRef.current) {
        environmentBackgroundTextureRef.current.dispose()
        environmentBackgroundTextureRef.current = null
      }
      if (environmentBackdropRef.current) {
        scene.remove(environmentBackdropRef.current)
        disposeObject3D(environmentBackdropRef.current)
        environmentBackdropRef.current = null
      }
      pmremGenerator.dispose()
      pmremGeneratorRef.current = null
      replaceSelectionOverlay(scene, selectionOverlayRef, null)
      replaceWireframeOverlay(scene, wireframeOverlayRef, null)
      restoreOriginalMaterials(
        modelRef.current,
        originalMeshMaterials,
        unlitMeshMaterials
      )
      disposeObject3D(modelRef.current)
      modelRef.current = null
      backendObjectLookupRef.current = new Map()
      backendObjectVisibilityRef.current = new WeakMap()
      disposeObject3D(gridRef.current)
      gridRef.current = null
      renderer.dispose()
      if (renderer.domElement.parentElement) {
        renderer.domElement.parentElement.removeChild(renderer.domElement)
      }
      scene.clear()
      sceneRef.current = null
      rendererRef.current = null
      cameraRef.current = null
      controlsRef.current = null
      transformControlsRef.current = null
      transformControlsHelperRef.current = null
      lightRef.current = null
      resizeRendererRef.current = null
      hasLoadedModelRef.current = false
      cameraViewRef.current = null
      hasUserCameraOverrideRef.current = false
      undoStackRef.current = []
      onUndoCountChangeRef.current?.(0)
      if (undoRef) {
        undoRef.current = null
      }
    }
  }, [viewerResetToken, undoRef])

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
    const { ambient, hemisphere, directional } = lightRef.current
    const proceduralSky = preset.proceduralSky
    const intensityScale = environment === 'none' ? 1 : resolvedEnvironmentLightIntensity

    if (proceduralSky) {
      const sunPosition = getProceduralSunPosition(proceduralSky)
      ambient.color = new THREE.Color('#ffffff')
      ambient.intensity = (useFallbackLighting ? preset.ambient : 0.015) * intensityScale
      hemisphere.color = new THREE.Color(proceduralSky.skyColor)
      hemisphere.groundColor = new THREE.Color(proceduralSky.groundColor)
      hemisphere.intensity = useFallbackLighting
        ? proceduralSky.hemisphereIntensity * intensityScale
        : Math.max(0.06, proceduralSky.hemisphereIntensity * 0.2 * intensityScale)
      directional.color = new THREE.Color(proceduralSky.sunColor)
      directional.intensity = useFallbackLighting
        ? proceduralSky.sunIntensity * intensityScale
        : Math.max(0.18, proceduralSky.sunIntensity * 0.22 * intensityScale)
      directional.position.copy(sunPosition).multiplyScalar(60)
    } else if (useFallbackLighting) {
      ambient.color = new THREE.Color(preset.color)
      hemisphere.color = new THREE.Color('#ffffff')
      hemisphere.groundColor = new THREE.Color('#111111')
      hemisphere.intensity = 0
      directional.color = new THREE.Color(preset.color)
      ambient.intensity = preset.ambient * intensityScale
      directional.intensity = preset.directional * intensityScale
    } else {
      ambient.color = new THREE.Color('#ffffff')
      hemisphere.color = new THREE.Color('#ffffff')
      hemisphere.groundColor = new THREE.Color('#111111')
      hemisphere.intensity = 0
      directional.color = new THREE.Color('#ffffff')
      ambient.intensity = 0.08 * intensityScale
      directional.intensity = 0.2 * intensityScale
    }

    if (rendererRef.current) {
      const baseExposure = useFallbackLighting ? preset.exposure : 0.82
      rendererRef.current.toneMappingExposure = environment === 'none'
        ? baseExposure
        : baseExposure * intensityScale
    }
  }, [environment, preset, resolvedEnvironmentLightIntensity, useFallbackLighting])

  useEffect(() => {
    const scene = sceneRef.current
    const pmremGenerator = pmremGeneratorRef.current
    if (!scene || !pmremGenerator) return
    const proceduralSky = preset.proceduralSky

    const disposeEnvironmentResources = () => {
      if (environmentRenderTargetRef.current) {
        environmentRenderTargetRef.current.dispose()
        environmentRenderTargetRef.current = null
      }
      if (environmentBackgroundTextureRef.current) {
        environmentBackgroundTextureRef.current.dispose()
        environmentBackgroundTextureRef.current = null
      }
      scene.environment = null
      ;(scene as THREE.Scene & { environmentIntensity?: number }).environmentIntensity = 0
    }

    const removeEnvironmentBackdrop = () => {
      if (!environmentBackdropRef.current) return
      scene.remove(environmentBackdropRef.current)
      disposeObject3D(environmentBackdropRef.current)
      environmentBackdropRef.current = null
    }

    const applyViewportBackground = () => {
      scene.background = new THREE.Color(viewportPaletteRef.current.background)
      scene.backgroundIntensity = 1
    }

    const loadToken = environmentLoadTokenRef.current + 1
    environmentLoadTokenRef.current = loadToken
    removeEnvironmentBackdrop()
    applyViewportBackground()

    if (proceduralSky) {
      disposeEnvironmentResources()

      const environmentScene = new THREE.Scene()
      const environmentSky = createProceduralSky(
        resolveProceduralSkyVisualConfig(proceduralSky, 'environment')
      )
      environmentScene.add(environmentSky)
      const renderTarget = pmremGenerator.fromScene(environmentScene, 0, 0.1, 1000)

      if (environmentLoadTokenRef.current !== loadToken) {
        renderTarget.dispose()
        disposeObject3D(environmentSky)
        return
      }

      environmentRenderTargetRef.current = renderTarget
      scene.environment = renderTarget.texture
      ;(scene as THREE.Scene & { environmentIntensity?: number }).environmentIntensity = useFallbackLighting
        ? proceduralSky.environmentIntensity * resolvedEnvironmentLightIntensity
        : Math.max(0.2, proceduralSky.environmentIntensity * 0.35 * resolvedEnvironmentLightIntensity)

      if (showHdriBackground) {
        const backgroundSky = createProceduralSky(
          resolveProceduralSkyVisualConfig(proceduralSky, 'background')
        )
        applyProceduralSkyBackgroundIntensity(backgroundSky, resolvedEnvironmentBackgroundIntensity)
        backgroundSky.frustumCulled = false
        backgroundSky.renderOrder = -1
        if (cameraRef.current) {
          backgroundSky.position.copy(cameraRef.current.position)
        }
        const backgroundMaterial = backgroundSky.material as THREE.ShaderMaterial
        backgroundMaterial.depthTest = false
        backgroundMaterial.depthWrite = false
        scene.add(backgroundSky)
        environmentBackdropRef.current = backgroundSky
      }

      disposeObject3D(environmentSky)
      return
    }

    if (!preset.hdriUrl) {
      disposeEnvironmentResources()
      applyViewportBackground()
      return
    }

    disposeEnvironmentResources()
    applyViewportBackground()
    ;(scene as THREE.Scene & { environmentIntensity?: number }).environmentIntensity =
      (useFallbackLighting ? 1.0 : 0.35) * resolvedEnvironmentLightIntensity

    const loader = new RGBELoader()
    loader.setDataType(THREE.HalfFloatType)
    loader.load(
      preset.hdriUrl,
      (texture: THREE.DataTexture) => {
        if (environmentLoadTokenRef.current !== loadToken) {
          texture.dispose()
          return
        }
        texture.mapping = THREE.EquirectangularReflectionMapping
        const renderTarget = pmremGenerator.fromEquirectangular(texture)

        if (environmentLoadTokenRef.current !== loadToken) {
          texture.dispose()
          renderTarget.dispose()
          return
        }

        if (environmentRenderTargetRef.current) {
          environmentRenderTargetRef.current.dispose()
        }
        if (environmentBackgroundTextureRef.current) {
          environmentBackgroundTextureRef.current.dispose()
        }
        environmentRenderTargetRef.current = renderTarget
        environmentBackgroundTextureRef.current = texture
        scene.environment = renderTarget.texture
        ;(scene as THREE.Scene & { environmentIntensity?: number }).environmentIntensity = useFallbackLighting
          ? resolvedEnvironmentLightIntensity
          : 0.35 * resolvedEnvironmentLightIntensity
        scene.background = showHdriBackground
          ? texture
          : new THREE.Color(viewportPaletteRef.current.background)
        scene.backgroundIntensity = showHdriBackground ? resolvedEnvironmentBackgroundIntensity : 1
      },
      undefined,
      (error: unknown) => {
        if (environmentLoadTokenRef.current !== loadToken) return
        disposeEnvironmentResources()
        applyViewportBackground()
        console.warn('Failed to load HDR environment map', error)
      }
    )
  }, [
    preset,
    resolvedEnvironmentBackgroundIntensity,
    resolvedEnvironmentLightIntensity,
    showHdriBackground,
    useFallbackLighting
  ])

  useEffect(() => {
    const scene = sceneRef.current
    if (!scene) return

    scene.background =
      showHdriBackground && environmentBackgroundTextureRef.current
        ? environmentBackgroundTextureRef.current
        : new THREE.Color(viewportPalette.background)
    scene.backgroundIntensity =
      showHdriBackground && environmentBackgroundTextureRef.current
        ? resolvedEnvironmentBackgroundIntensity
        : 1

    replaceViewportGrid(
      scene,
      gridRef,
      showGrid ? buildViewportGrid(modelRef.current, viewportPalette) : null
    )
  }, [resolvedEnvironmentBackgroundIntensity, showGrid, showHdriBackground, viewportPalette])

  useEffect(() => {
    const scene = sceneRef.current
    if (!scene) return

    replaceWireframeOverlay(
      scene,
      wireframeOverlayRef,
      effectiveWireframe && modelRef.current
        ? buildWireframeOverlay(modelRef.current, viewportPalette.wireframe)
        : null
    )
  }, [effectiveWireframe, viewportPalette])

  useEffect(() => {
    const scene = sceneRef.current
    const camera = cameraRef.current
    const controls = controlsRef.current
    if (!scene || !camera || !controls) return

    const resetToDefault = () => {
      if (modelRef.current) {
        restoreOriginalMaterials(
          modelRef.current,
          originalMeshMaterialsRef.current,
          unlitMeshMaterialsRef.current
        )
        disposeObject3D(modelRef.current)
        scene.remove(modelRef.current)
        modelRef.current = null
      }
      backendObjectLookupRef.current = new Map()
      backendObjectVisibilityRef.current = new WeakMap()
      replaceSelectionOverlay(scene, selectionOverlayRef, null)
      replaceWireframeOverlay(scene, wireframeOverlayRef, null)
      if (transformControlsRef.current) {
        transformControlsRef.current.detach()
        transformControlsRef.current.visible = false
      }
      if (transformControlsHelperRef.current) {
        transformControlsHelperRef.current.visible = false
      }
      const palette = viewportPaletteRef.current
      replaceViewportGrid(
        scene,
        gridRef,
        showGridRef.current ? buildViewportGrid(null, palette) : null
      )
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
      refreshTransformControls()
    }

    if (!gltfUrl) {
      resetToDefault()
      return
    }

    const loader = new GLTFLoader()
    const loadToken = loadTokenRef.current + 1
    loadTokenRef.current = loadToken
    const hasLoadedModel = hasLoadedModelRef.current
    const hasUserCameraOverride = hasUserCameraOverrideRef.current
    const preservedView =
      hasLoadedModel &&
      !alwaysAutoFrameCameraRef.current &&
      hasUserCameraOverride
        ? cameraViewRef.current
        : null
    debugCameraReload('load-start', {
      hasLoadedModel,
      hasUserCameraOverride,
      usedPreservedView: Boolean(preservedView),
      cameraPosition: camera.position,
      cameraTarget: controls.target
    })

    loader.load(
      gltfUrl,
      (gltf: { scene: THREE.Object3D }) => {
        if (loadTokenRef.current !== loadToken) return

        if (modelRef.current) {
          restoreOriginalMaterials(
            modelRef.current,
            originalMeshMaterialsRef.current,
            unlitMeshMaterialsRef.current
          )
          disposeObject3D(modelRef.current)
          scene.remove(modelRef.current)
        }
        replaceSelectionOverlay(scene, selectionOverlayRef, null)

        const hasEmbeddedLights = sceneContainsLights(gltf.scene)
        if (hasEmbeddedLights) {
          normalizeEmbeddedLightIntensities(gltf.scene)
        }
        setUseFallbackLighting(!hasEmbeddedLights)
        modelRef.current = gltf.scene
        backendObjectLookupRef.current = buildBackendObjectLookup(gltf.scene)
        backendObjectVisibilityRef.current = new WeakMap()
        applyShadingMode()
        scene.add(gltf.scene)

        const box = new THREE.Box3().setFromObject(gltf.scene)
        const size = new THREE.Vector3()
        const center = new THREE.Vector3()
        box.getSize(size)
        box.getCenter(center)
        const palette = viewportPaletteRef.current
        replaceViewportGrid(
          scene,
          gridRef,
          showGridRef.current ? buildViewportGrid(gltf.scene, palette) : null
        )
        replaceWireframeOverlay(
          scene,
          wireframeOverlayRef,
          showWireframeOverlayRef.current
            ? buildWireframeOverlay(gltf.scene, palette.wireframe)
            : null
        )

        if (preservedView) {
          camera.position.copy(preservedView.position)
          controls.target.copy(preservedView.target)
          const distance = camera.position.distanceTo(controls.target)
          camera.near = Math.max(distance / 1000, 0.05)
          camera.far = Math.max(distance * 25, 1000)
          camera.updateProjectionMatrix()
          controls.update()
          debugCameraReload('restore-preserved-view', {
            hasLoadedModel,
            hasUserCameraOverride,
            usedPreservedView: true,
            cameraPosition: camera.position,
            cameraTarget: controls.target
          })
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
        refreshSelectionOverlay()
        refreshTransformControls()
        if (
          appliedFocusViewportRequestRef.current < focusViewportRequestRef.current &&
          selectedBackendObjectIdRef.current
        ) {
          focusSelectedObject()
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
          {showFullscreenButton && onToggleFullscreen && (
            <button
              className="ghost-btn icon-btn viewer-fullscreen-btn"
              onClick={onToggleFullscreen}
              title={isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}
              aria-label={isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}
            >
              {isFullscreen ? (
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="5.5 1 5.5 5.5 1 5.5" />
                  <polyline points="10.5 15 10.5 10.5 15 10.5" />
                  <polyline points="15 5.5 10.5 5.5 10.5 1" />
                  <polyline points="1 10.5 5.5 10.5 5.5 15" />
                </svg>
              ) : (
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="1 5.5 1 1 5.5 1" />
                  <polyline points="15 10.5 15 15 10.5 15" />
                  <polyline points="10.5 1 15 1 15 5.5" />
                  <polyline points="5.5 15 1 15 1 10.5" />
                </svg>
              )}
            </button>
          )}
          {headerTrailingControls}
        </div>
      </div>
      <div className={`viewer viewport-${resolvedViewportTheme}`}>
        {!gltfUrl && <div className="viewer-placeholder">No model loaded</div>}
        <div className="viewer-canvas" ref={containerRef} tabIndex={0} />
        {footerControls ? <div className="viewer-bottom-bar">{footerControls}</div> : null}
      </div>
    </div>
  )
}
