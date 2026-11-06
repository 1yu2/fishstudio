import { fileURLToPath } from 'node:url'

export default {
  root: fileURLToPath(new URL('../frontend', import.meta.url)),
  // 禁止 Vite 自动读取 frontend/.env*。
  envDir: false,
  cacheDir: fileURLToPath(new URL('.runtime/vite', import.meta.url)),
  server: {
    host: '127.0.0.1', port: 3031, strictPort: true,
    proxy: Object.fromEntries(['/api', '/storage', '/ws'].map(prefix => [prefix, {
      target: 'http://127.0.0.1:8031', changeOrigin: true, ws: prefix === '/ws',
    }])),
  },
}
