import React, { Suspense, useRef } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { OrbitControls, useGLTF } from '@react-three/drei'
import { useLoader } from '@react-three/fiber'
import { OBJLoader } from 'three/examples/jsm/loaders/OBJLoader.js'
import { MTLLoader } from 'three/examples/jsm/loaders/MTLLoader.js'
import { Object3D } from 'three'
import './Model3DViewer.css'

type Model3DViewerProps = {
  modelUrl: string
  format: 'obj' | 'glb'
  mtlUrl?: string
  textureUrl?: string
  width?: number
  height?: number
}

// OBJ模型加载组件（支持材质和纹理）
function OBJModel({ url, mtlUrl, textureUrl }: { url: string; mtlUrl?: string; textureUrl?: string }) {
  const meshRef = useRef<Object3D>(null)
  const [obj, setObj] = React.useState<Object3D | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  
  React.useEffect(() => {
    const loadModel = async () => {
      try {
        const loader = new OBJLoader()
        
        // 如果有MTL文件，先加载材质
        if (mtlUrl) {
          try {
            const mtlLoader = new MTLLoader()
            // 设置材质文件的base路径（用于加载纹理）
            // 从MTL URL提取目录路径
            const mtlUrlObj = new URL(mtlUrl, window.location.origin)
            const basePath = mtlUrlObj.pathname.substring(0, mtlUrlObj.pathname.lastIndexOf('/') + 1)
            // 设置资源路径为完整的URL路径
            mtlLoader.setResourcePath(window.location.origin + basePath)
            
            const materials = await new Promise((resolve, reject) => {
              mtlLoader.load(
                mtlUrl,
                (materials) => {
                  materials.preload()
                  resolve(materials)
                },
                undefined,
                reject
              )
            })
            
            loader.setMaterials(materials as any)
            console.log('✅ MTL材质已加载:', mtlUrl, '资源路径:', window.location.origin + basePath)
          } catch (mtlError) {
            console.warn('⚠️ 加载MTL材质失败，将使用默认材质:', mtlError)
          }
        }
        
        // 加载OBJ模型
        const object = await new Promise<Object3D>((resolve, reject) => {
          loader.load(
            url,
            (object) => resolve(object),
            undefined,
            reject
          )
        })
        
        setObj(object)
        console.log('✅ OBJ模型已加载:', url)
      } catch (err) {
        console.error('❌ 加载OBJ模型失败:', err)
        setError(err instanceof Error ? err.message : '加载模型失败')
      }
    }
    
    loadModel()
  }, [url, mtlUrl, textureUrl])

  // 自动旋转
  useFrame((state, delta) => {
    if (meshRef.current) {
      meshRef.current.rotation.y += delta * 0.2
    }
  })

  if (error) {
    return (
      <mesh>
        <boxGeometry args={[1, 1, 1]} />
        <meshBasicMaterial color="red" />
      </mesh>
    )
  }

  if (!obj) {
    return <LoadingPlaceholder />
  }

  return (
    <primitive 
      object={obj} 
      ref={meshRef}
      scale={1}
      position={[0, 0, 0]}
    />
  )
}

// GLB模型加载组件
function GLBModel({ url }: { url: string }) {
  const { scene } = useGLTF(url)
  const meshRef = useRef<Object3D>(null)

  // 自动旋转
  useFrame((state, delta) => {
    if (meshRef.current) {
      meshRef.current.rotation.y += delta * 0.2
    }
  })

  return (
    <primitive 
      object={scene} 
      ref={meshRef}
      scale={1}
      position={[0, 0, 0]}
    />
  )
}

// 加载占位符
function LoadingPlaceholder() {
  return (
    <mesh>
      <boxGeometry args={[1, 1, 1]} />
      <meshStandardMaterial color="#888888" wireframe />
    </mesh>
  )
}

export default function Model3DViewer({ 
  modelUrl, 
  format, 
  mtlUrl,
  textureUrl,
  width = 400, 
  height = 400 
}: Model3DViewerProps) {
  return (
    <div className="model-3d-viewer" style={{ width, height }}>
      <Canvas
        camera={{ position: [0, 0, 5], fov: 50 }}
        gl={{ antialias: true }}
      >
        <ambientLight intensity={0.5} />
        <directionalLight position={[10, 10, 5]} intensity={1} />
        <pointLight position={[-10, -10, -5]} intensity={0.5} />
        
        <Suspense fallback={<LoadingPlaceholder />}>
          {format === 'obj' ? (
            <OBJModel url={modelUrl} mtlUrl={mtlUrl} textureUrl={textureUrl} />
          ) : (
            <GLBModel url={modelUrl} />
          )}
        </Suspense>
        
        <OrbitControls 
          enablePan={true}
          enableZoom={true}
          enableRotate={true}
        />
      </Canvas>
    </div>
  )
}

