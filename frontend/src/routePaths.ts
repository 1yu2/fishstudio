export const paths = {
  home: '/',
  projects: '/projects',
  settings: '/settings',
  canvas: (canvasId: string) => `/canvas/${encodeURIComponent(canvasId)}`,
  nodeCanvas: (canvasId: string) => `/studio/${encodeURIComponent(canvasId)}`,
}
