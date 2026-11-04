import { workspaceApi } from '../../api/workspace'
import { POLL_INTERVAL_MS, usePolling } from '../../hooks/usePolling'

export { POLL_INTERVAL_MS }

const fetchOverview = () => workspaceApi.overview()

/** 加载工作台总览，每 10 秒轮询一次；页面不可见时暂停，重新可见时立即刷新。 */
export function useWorkspaceOverview() {
  return usePolling(fetchOverview, '工作台加载失败')
}
