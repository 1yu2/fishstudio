import { spawn } from 'node:child_process'
import { createServer } from 'node:net'
import { mkdir } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const here = path.dirname(fileURLToPath(import.meta.url))
const root = path.dirname(here)
// 只传递运行所需变量，绝不继承厂商 Key、代理或根目录 .env。
const env = Object.fromEntries(['PATH', 'HOME', 'TMPDIR', 'SYSTEMROOT'].filter(k => process.env[k]).map(k => [k, process.env[k]]))
Object.assign(env, {
  PYTHONPATH: path.join(root, 'backend'),
  DATABASE_URL: process.env.E2E_DATABASE_URL || 'postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/fishstudio_test_c2',
  JWT_SECRET: 'e2e-only-jwt-secret-never-production',
  ADMIN_USERNAME: 'e2e_admin', ADMIN_PASSWORD: 'e2e-admin-password',
  ALLOW_MOCK_PROVIDERS: 'true', MOCK_PROVIDER_DELAY_POLLS: '0',
  CORS_ORIGINS: 'http://127.0.0.1:3031',
  EMBEDDED_WORKER: 'true', ENVIRONMENT: 'test',
  PYTHONDONTWRITEBYTECODE: '1',
})
const python = process.env.E2E_PYTHON || 'python3'
const children = []
let stopping = false
function stop(code = 0) {
  if (stopping) return
  stopping = true
  for (const child of children) child.kill('SIGTERM')
  const timer = setTimeout(() => {
    for (const child of children) child.kill('SIGKILL')
    process.exit(code)
  }, 8000)
  Promise.all(children.map(child => (child.exitCode !== null || child.signalCode !== null) ? Promise.resolve() : new Promise(resolve => child.once('exit', resolve))))
    .then(() => { clearTimeout(timer); process.exit(code) })
}
process.on('SIGTERM', () => stop())
process.on('SIGINT', () => stop())
function run(command, args) {
  const child = spawn(command, args, { cwd: path.join(here, '.runtime'), env, stdio: 'inherit' })
  children.push(child)
  child.on('error', error => { console.error(error.message); stop(1) })
  child.on('exit', code => { if (!stopping) stop(code || 1) })
  return child
}
// 两个端口都必须空闲，避免误用或清理其他服务。
for (const port of [8031, 3031]) {
  await new Promise((resolve, reject) => {
    const server = createServer()
    server.once('error', reject)
    server.listen(port, '127.0.0.1', () => server.close(resolve))
  })
}
await mkdir(path.join(here, '.runtime'), { recursive: true })
run(python, [path.join(here, 'backend.py')])
try {
  const deadline = Date.now() + 100_000
  while (true) {
    try { if ((await fetch('http://127.0.0.1:8031/ready')).ok) break } catch {}
    if (Date.now() > deadline) throw new Error('E2E backend readiness timeout')
    await new Promise(resolve => setTimeout(resolve, 250))
  }
  run(process.execPath, [path.join(root, 'frontend/node_modules/vite/bin/vite.js'), '--config', path.join(here, 'vite.config.mjs'), '--mode', 'e2e'])
} catch (error) { console.error(error.message); stop(1) }
