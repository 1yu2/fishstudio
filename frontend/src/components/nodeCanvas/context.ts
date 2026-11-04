import { createContext, useContext } from 'react'
import type { BatchScope, CanvasModels, CanvasRuns, Quote, RunRequest, StoredEdge, StoredNode } from '../../api/nodeCanvas'

export interface CanvasActions {
  canvasId: string
  aspectRatio: string
  models: CanvasModels | null
  runs: CanvasRuns | null
  nodes: StoredNode[]
  edges: StoredEdge[]
  /** 角色节点 id（用于节点上的“角色”标签） */
  characterIds: Set<string>
  updateData: (nodeId: string, patch: Record<string, unknown>) => void
  run: (nodeId: string, request: RunRequest) => Promise<void>
  upload: (nodeId: string, file: File) => Promise<void>
  cancel: (jobId: string) => Promise<void>
  quote: (request: RunRequest) => Promise<Quote>
  /** 打开批量运行确认（先报价再确认）；downstream 需要给节点 id */
  runBatch: (scope: BatchScope, nodeId?: string) => void
}

export const CanvasContext = createContext<CanvasActions | null>(null)

export function useCanvas(): CanvasActions {
  const value = useContext(CanvasContext)
  if (!value) throw new Error('useCanvas 需要在 CanvasContext 内使用')
  return value
}
