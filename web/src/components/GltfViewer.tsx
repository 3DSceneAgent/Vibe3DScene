import { useEffect, useMemo, useRef } from 'react'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls'

type EnvironmentPreset = 'studio' | 'warm' | 'cool'

type GltfViewerProps = {
  gltfUrl: string | null
  environment: EnvironmentPreset
}

const environmentPresets: Record<EnvironmentPreset, { ambient: number; directional: number; color: string }> =
  {
    studio: { ambient: 0.55, directional: 1.2, color: '#ffffff' },
    warm: { ambient: 0.6, directional: 1.1, color: '#ffd8b2' },
    cool: { ambient: 0.5, directional: 1.3, color: '#cfe6ff' }
  }

export function GltfViewer({ gltfUrl, environment }: GltfViewerProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const sceneRef = useRef<THREE.Scene | null>(null)
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null)
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null)
  const controlsRef = useRef<OrbitControls | null>(null)
  const modelRef = useRef<THREE.Object3D | null>(null)
  const lightRef = useRef<{ ambient: THREE.AmbientLight; directional: THREE.DirectionalLight } | null>(
    null
  )

  const preset = useMemo(() => environmentPresets[environment], [environment])

  useEffect(() => {
    if (!containerRef.current) return

    const scene = new THREE.Scene()
    scene.background = new THREE.Color(0x0f1117)

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    renderer.setPixelRatio(window.devicePixelRatio)
    renderer.setSize(containerRef.current.clientWidth, containerRef.current.clientHeight)
    containerRef.current.appendChild(renderer.domElement)

    const camera = new THREE.PerspectiveCamera(
      45,
      containerRef.current.clientWidth / containerRef.current.clientHeight,
      0.1,
      2000
    )
    camera.position.set(4, 3, 6)

    const ambient = new THREE.AmbientLight(preset.color, preset.ambient)
    const directional = new THREE.DirectionalLight(preset.color, preset.directional)
    directional.position.set(6, 10, 4)
    scene.add(ambient, directional)

    const grid = new THREE.GridHelper(10, 20, 0x2f3547, 0x1c2030)
    grid.position.y = -0.01
    scene.add(grid)

    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.08

    sceneRef.current = scene
    rendererRef.current = renderer
    cameraRef.current = camera
    controlsRef.current = controls
    lightRef.current = { ambient, directional }

    let animationFrame = 0
    const animate = () => {
      controls.update()
      renderer.render(scene, camera)
      animationFrame = window.requestAnimationFrame(animate)
    }
    animate()

    const handleResize = () => {
      if (!containerRef.current || !rendererRef.current || !cameraRef.current) return
      const width = containerRef.current.clientWidth
      const height = containerRef.current.clientHeight
      rendererRef.current.setSize(width, height)
      cameraRef.current.aspect = width / height
      cameraRef.current.updateProjectionMatrix()
    }

    window.addEventListener('resize', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
      window.cancelAnimationFrame(animationFrame)
      controls.dispose()
      renderer.dispose()
      if (renderer.domElement.parentElement) {
        renderer.domElement.parentElement.removeChild(renderer.domElement)
      }
      scene.clear()
    }
  }, [])

  useEffect(() => {
    if (!lightRef.current) return
    const { ambient, directional } = lightRef.current
    ambient.color = new THREE.Color(preset.color)
    directional.color = new THREE.Color(preset.color)
    ambient.intensity = preset.ambient
    directional.intensity = preset.directional
  }, [preset])

  useEffect(() => {
    if (!gltfUrl || !sceneRef.current || !cameraRef.current || !controlsRef.current) return

    const loader = new GLTFLoader()
    loader.load(
      gltfUrl,
      (gltf) => {
        const scene = sceneRef.current
        if (!scene) return
        if (modelRef.current) {
          scene.remove(modelRef.current)
        }
        modelRef.current = gltf.scene
        scene.add(gltf.scene)

        const box = new THREE.Box3().setFromObject(gltf.scene)
        const size = new THREE.Vector3()
        const center = new THREE.Vector3()
        box.getSize(size)
        box.getCenter(center)

        const maxDim = Math.max(size.x, size.y, size.z)
        const camera = cameraRef.current
        const controls = controlsRef.current
        if (camera && controls) {
          const fov = (camera.fov * Math.PI) / 180
          let cameraZ = Math.abs((maxDim / 2) / Math.tan(fov / 2))
          cameraZ *= 1.8
          camera.position.set(center.x + cameraZ, center.y + maxDim * 0.3, center.z + cameraZ)
          camera.near = Math.max(maxDim / 100, 0.1)
          camera.far = Math.max(maxDim * 100, 2000)
          camera.updateProjectionMatrix()
          controls.target.copy(center)
          controls.update()
        }
      },
      undefined,
      (error) => {
        console.error('Failed to load GLTF', error)
      }
    )
  }, [gltfUrl])

  return (
    <div className="panel">
      <div className="panel-header">
        <div className="panel-title">3D Viewport</div>
        <div className="panel-subtitle">GLB streaming from agent</div>
      </div>
      <div className="viewer">
        {!gltfUrl && <div className="viewer-placeholder">No model loaded</div>}
        <div className="viewer-canvas" ref={containerRef} />
      </div>
    </div>
  )
}
