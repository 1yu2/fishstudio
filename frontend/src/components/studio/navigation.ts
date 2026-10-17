/**
 * Studio 路由导航辅助（P2-8）：独立于 StudioHome 组件文件，
 * 避免 HomePage 等静态引用时把 StudioHome 拉进入口 chunk（动态分包失效）。
 */

export function goToStudioHome() {
  window.history.pushState({}, '', '/projects/drama')
  window.dispatchEvent(new PopStateEvent('popstate'))
}

export function goToStudioProject(projectId: string) {
  window.history.pushState({}, '', `/projects/drama/${encodeURIComponent(projectId)}`)
  window.dispatchEvent(new PopStateEvent('popstate'))
}
