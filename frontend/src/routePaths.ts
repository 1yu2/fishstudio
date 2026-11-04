export const paths = {
  home: '/',
  projects: '/projects',
  library: '/library',
  settings: '/settings',
  nodeCanvas: (canvasId: string) => `/studio/${encodeURIComponent(canvasId)}`,
}
