import { useCallback } from 'react'
import { workspaceApi } from '../../api/workspace'
import { usePolling } from '../../hooks/usePolling'

/** 项目页顶栏 / 步骤条 / 底栏的数据：10 秒轮询，页面隐藏时暂停。 */
export function useProjectSummary(canvasId: string) {
  const fetcher = useCallback(() => workspaceApi.project(canvasId), [canvasId])
  return usePolling(fetcher, '项目加载失败')
}
