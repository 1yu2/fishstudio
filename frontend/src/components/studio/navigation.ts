/**
 * Studio 路由导航辅助（P2-8）：独立于 StudioHome 组件文件，
 * 避免 HomePage 等静态引用时把 StudioHome 拉进入口 chunk（动态分包失效）。
 */

export function goToStudioHome() {
  const url = new URL(window.location.href)
  url.searchParams.delete('canvasId')
  url.searchParams.delete('projectId')
  url.searchParams.set('page', 'studio')
  window.history.pushState({}, '', url.toString())
  window.dispatchEvent(new PopStateEvent('popstate'))
}

export function goToStudioProject(projectId: string) {
  const url = new URL(window.location.href)
  url.searchParams.delete('canvasId')
  url.searchParams.set('page', 'studio')
  url.searchParams.set('projectId', projectId)
  window.history.pushState({}, '', url.toString())
  window.dispatchEvent(new PopStateEvent('popstate'))
}
