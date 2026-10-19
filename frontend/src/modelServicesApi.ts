import { apiFetch } from './api'

export interface ModelDeployment {
  id: string
  name: string
  slug: string
  driver: string
  modality: string
  model: string
  base_url: string
  credential_configured: boolean
  enabled: boolean
  priority: number
  config: Record<string, unknown>
  capabilities: Record<string, unknown>
  pricing: Record<string, unknown>
  revision: number
}

export interface RuntimeDeployment extends ModelDeployment {
  available: boolean
}

export type RouteTier = 'draft' | 'preview' | 'final'

export interface ModelRoute {
  purpose: string
  tier: RouteTier
  modality: string
  primary_deployment: string
  fallback_deployments: string[]
  enabled: boolean
  revision: number
}

export type Capability = 'chat' | 'embedding' | 'rerank' | 'asr' | 'tts' | 'image' | 'video' | '3d'

export interface ProviderType {
  id: string
  label: string
  capabilities: Capability[]
  drivers: Partial<Record<Capability, string>>
  default_base_url: string
  default_models_endpoint: string
  lists_models: boolean
}

export interface ProviderModel extends ModelDeployment {
  capability: Capability
  available?: boolean | null
}

export interface ModelProvider {
  id: string
  slug: string
  name: string
  provider_type: ProviderType
  base_url: string
  api_key_env: string
  credential_configured: boolean
  models_endpoint: string
  capabilities: Capability[]
  enabled: boolean
  revision: number
  models: ProviderModel[]
}

export interface ProviderInput {
  slug?: string
  name?: string
  provider_type?: string
  base_url?: string
  api_key_env?: string
  api_key?: string
  models_endpoint?: string
  capabilities?: Capability[]
  enabled?: boolean
}

export interface RemoteModel {
  model: string
  capability: Capability
  added: boolean
}

export interface ModelInput {
  capability: Capability
  model: string
  name?: string
  config?: Record<string, unknown>
  capabilities?: Record<string, unknown>
  pricing?: Record<string, unknown>
  enabled?: boolean
}

export interface PurposeDefinition {
  purpose: string
  modality: string
  required_capabilities: string[]
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, init)
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body?.error?.message || body?.detail?.message || body?.detail || '请求失败')
  }
  return response.json()
}

const jsonBody = (method: string, body: unknown): RequestInit => ({
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
})

export const providersApi = {
  list: () => json<ModelProvider[]>('/api/model-providers'),
  types: () => json<{ capabilities: Capability[]; types: ProviderType[] }>('/api/model-providers/types'),
  create: (body: ProviderInput) => json<ModelProvider>('/api/model-providers', jsonBody('POST', body)),
  update: (slug: string, body: ProviderInput) => json<ModelProvider>(`/api/model-providers/${slug}`, jsonBody('PATCH', body)),
  remove: async (slug: string) => {
    const response = await apiFetch(`/api/model-providers/${slug}`, { method: 'DELETE' })
    if (!response.ok) {
      const body = await response.json().catch(() => ({}))
      throw new Error(body?.error?.message || body?.detail || '删除失败')
    }
  },
  credential: async (slug: string, apiKey: string) => {
    const response = await apiFetch(`/api/model-providers/${slug}/credential`, jsonBody('PUT', { api_key: apiKey }))
    if (!response.ok) throw new Error('API Key 保存失败')
  },
  check: (slug: string) => json<{ ok: boolean; message: string }>(`/api/model-providers/${slug}/check`, { method: 'POST' }),
  remoteModels: (slug: string) => json<RemoteModel[]>(`/api/model-providers/${slug}/remote-models`),
  addModel: (slug: string, body: ModelInput) => json<ModelProvider>(`/api/model-providers/${slug}/models`, jsonBody('POST', body)),
}

export const modelServicesApi = {
  deployments: () => json<ModelDeployment[]>('/api/model-deployments'),
  runtime: () => json<{ items: RuntimeDeployment[]; last_reload_failure?: { reason: string } }>('/api/model-runtime/deployments'),
  create: (body: Record<string, unknown>) => json<ModelDeployment>('/api/model-deployments', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }),
  update: (slug: string, body: Record<string, unknown>) => json<ModelDeployment>(`/api/model-deployments/${slug}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }),
  credential: async (slug: string, apiKey: string) => {
    const response = await apiFetch(`/api/model-deployments/${slug}/credential`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ api_key: apiKey }),
    })
    if (!response.ok) throw new Error('API Key 保存失败')
  },
  enable: (slug: string, enabled: boolean) => json<ModelDeployment>(`/api/model-deployments/${slug}/enabled`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled }),
  }),
  remove: async (slug: string) => {
    const response = await apiFetch(`/api/model-deployments/${slug}`, { method: 'DELETE' })
    if (!response.ok) {
      const body = await response.json().catch(() => ({}))
      const purposes = body?.error?.message?.purposes || body?.detail?.purposes
      throw new Error(purposes ? `仍被用途绑定引用：${purposes.join('、')}` : '删除失败')
    }
  },
  health: (slug: string) => json<Record<string, unknown>>(`/api/model-runtime/deployments/${slug}/health`),
  routes: () => json<ModelRoute[]>('/api/model-routes'),
  purposes: () => json<PurposeDefinition[]>('/api/model-routes/purposes'),
  saveRoute: (route: Omit<ModelRoute, 'revision'>) => json<ModelRoute>(`/api/model-routes/${route.purpose}?tier=${route.tier}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(route),
  }),
  removeRoute: async (purpose: string, tier: RouteTier) => {
    const response = await apiFetch(`/api/model-routes/${purpose}?tier=${tier}`, { method: 'DELETE' })
    if (!response.ok && response.status !== 404) throw new Error('清除档位绑定失败')
  },
}
