import { createContext, useContext } from 'react'
import type { CanvasModels, CanvasRuns, Quote, RunRequest, StoredEdge, StoredNode } from '../../api/nodeCanvas'

export interface CanvasActions {
  canvasId: string
  aspectRatio: string
  models: CanvasModels | null
  runs: CanvasRuns | null
  nodes: StoredNode[]
  edges: StoredEdge[]
  updateData: (nodeId: string, patch: Record<string, unknown>) => void
  run: (nodeId: string, request: RunRequest) => Promise<void>
  upload: (nodeId: string, file: File) => Promise<void>
  quote: (request: RunRequest) => Promise<Quote>
}

export const CanvasContext = createContext<CanvasActions | null>(null)

export function useCanvas(): CanvasActions {
  const value = useContext(CanvasContext)
  if (!value) throw new Error('useCanvas 需要在 CanvasContext 内使用')
  return value
}
