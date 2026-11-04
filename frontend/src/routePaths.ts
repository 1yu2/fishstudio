export const paths = {
  home: '/',
  projects: '/projects',
  library: '/library',
  settings: '/settings',
  // 入口：任何地方打开项目都走这个；默认落点由 routing 决定（目前是画布视图）
  nodeCanvas: (canvasId: string) => `/studio/${encodeURIComponent(canvasId)}`,
  projectCanvas: (canvasId: string) => `/studio/${encodeURIComponent(canvasId)}/canvas`,
  projectStep: (canvasId: string, step: string) => `/studio/${encodeURIComponent(canvasId)}/${step}`,
}
