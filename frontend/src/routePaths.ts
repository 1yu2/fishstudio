export const paths = {
  home: '/',
  projects: '/projects',
  dramaHome: '/projects/drama',
  settings: '/settings',
  canvas: (canvasId: string) => `/canvas/${encodeURIComponent(canvasId)}`,
  dramaProject: (projectId: string) => `/projects/drama/${encodeURIComponent(projectId)}`,
}
