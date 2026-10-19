import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ModelProvider, ModelRoute, ProviderModel, ProviderType, PurposeDefinition } from '../../modelServicesApi'
import { modelServicesApi, providersApi } from '../../modelServicesApi'
import DefaultModels from './DefaultModels'
import ModelDialog from './ModelDialog'
import ModelServicesPanel from './ModelServicesPanel'
import { capabilityTags, priceLabel, supportsCapabilities } from './labels'

vi.mock('../../modelServicesApi', () => ({
  providersApi: {
    list: vi.fn(), types: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn(),
    credential: vi.fn(), check: vi.fn(), remoteModels: vi.fn(), addModel: vi.fn(),
  },
  modelServicesApi: {
    routes: vi.fn(), purposes: vi.fn(), runtime: vi.fn(), saveRoute: vi.fn(), removeRoute: vi.fn(),
    update: vi.fn(), enable: vi.fn(), remove: vi.fn(),
  },
}))

const openaiType: ProviderType = {
  id: 'openai', label: 'OpenAI Completions API', capabilities: ['chat', 'embedding', 'rerank', 'asr'],
  drivers: { chat: 'openai_compatible', embedding: 'generic_model', rerank: 'generic_model', asr: 'generic_model' },
  default_base_url: 'https://api.openai.com/v1', default_models_endpoint: '/models', lists_models: true,
}
const arkType: ProviderType = {
  id: 'volcengine-ark', label: 'Volcengine Ark API', capabilities: ['chat', 'embedding', 'image', 'video'],
  drivers: { chat: 'openai_compatible', embedding: 'generic_model', image: 'volcano_image', video: 'ark_video' },
  default_base_url: 'https://ark.cn-beijing.volces.com/api/v3', default_models_endpoint: '/models', lists_models: true,
}

function model(overrides: Partial<ProviderModel>): ProviderModel {
  return {
    id: 'm', name: 'm', slug: 'm', driver: 'ark_video', modality: 'video', capability: 'video', model: 'm', base_url: '',
    credential_configured: true, enabled: true, priority: 100, config: {}, capabilities: {}, pricing: {}, revision: 1, available: true,
    ...overrides,
  }
}

const seedance = model({ slug: 'ark-seedance', name: 'Seedance 1.5 Pro', model: 'doubao-seedance-1-5-pro', capabilities: { modes: ['text', 'first_frame'] }, pricing: { output_per_million_tokens: '15' } })
const ark: ModelProvider = {
  id: 'p1', slug: 'volcengine-ark', name: 'Volcengine Ark', provider_type: arkType, base_url: arkType.default_base_url, api_key_env: 'VOLCANO_API_KEY',
  credential_configured: false, models_endpoint: '/models', capabilities: ['chat', 'video'], enabled: true, revision: 1,
  models: [seedance, model({ slug: 'ark-chat', name: 'doubao', capability: 'chat', modality: 'chat', driver: 'openai_compatible', enabled: false })],
}
const siliconflow: ModelProvider = {
  id: 'p2', slug: 'siliconflow-cn', name: 'SiliconFlow', provider_type: openaiType, base_url: 'https://api.siliconflow.cn/v1', api_key_env: 'SILICONFLOW_API_KEY',
  credential_configured: true, models_endpoint: '/models', capabilities: ['chat', 'embedding', 'rerank'], enabled: true, revision: 1, models: [],
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(providersApi.list).mockResolvedValue([ark, siliconflow])
  vi.mocked(providersApi.types).mockResolvedValue({ capabilities: ['chat', 'embedding', 'rerank', 'asr', 'tts', 'image', 'video', '3d'], types: [openaiType, arkType] })
  vi.mocked(modelServicesApi.routes).mockResolvedValue([])
  vi.mocked(modelServicesApi.purposes).mockResolvedValue([])
  vi.mocked(modelServicesApi.runtime).mockResolvedValue({ items: [] })
})

describe('labels', () => {
  it('describes capabilities and prices', () => {
    expect(capabilityTags({ modes: ['text', 'first_frame', 'first_last_frame'], tools: true })).toEqual(['首帧', '首尾帧', '工具'])
    expect(priceLabel({ input_per_million_tokens: '2', output_per_million_tokens: '8' }, 'chat')).toBe('¥2 / ¥8 每百万 Token')
    expect(priceLabel({})).toBe('未定价')
    expect(supportsCapabilities({ tools: true }, ['tools'])).toBe(true)
  })
})

describe('provider cards', () => {
  it('shows id, base url, capabilities, enabled count and status', async () => {
    render(<ModelServicesPanel />)

    const card = await screen.findByRole('article', { name: 'Volcengine Ark' })
    expect(within(card).getByText('volcengine-ark')).toBeInTheDocument()
    expect(within(card).getByText('https://ark.cn-beijing.volces.com/api/v3')).toBeInTheDocument()
    expect(within(card).getByText('chat, video')).toBeInTheDocument()
    expect(within(card).getByText('（已启用 1 个）')).toBeInTheDocument()
    expect(within(card).getByRole('img', { name: '缺少 VOLCANO_API_KEY' })).toBeInTheDocument()
    expect(within(screen.getByRole('article', { name: 'SiliconFlow' })).getByRole('img', { name: '可用' })).toBeInTheDocument()
  })

  it('filters by search and toggles a provider', async () => {
    render(<ModelServicesPanel />)

    fireEvent.change(await screen.findByLabelText('搜索供应商'), { target: { value: 'rerank' } })
    expect(screen.queryByRole('article', { name: 'Volcengine Ark' })).not.toBeInTheDocument()
    fireEvent.click(within(screen.getByRole('article', { name: 'SiliconFlow' })).getByRole('switch'))
    await waitFor(() => expect(providersApi.update).toHaveBeenCalledWith('siliconflow-cn', { enabled: false }))
  })
})

describe('new provider dialog', () => {
  it('creates a provider with derived key env and chosen capabilities', async () => {
    const created = { ...siliconflow, slug: 'jd-ds', name: 'jd-ds' }
    vi.mocked(providersApi.create).mockImplementation(async () => {
      vi.mocked(providersApi.list).mockResolvedValue([ark, siliconflow, created])
      return created
    })
    render(<ModelServicesPanel />)

    fireEvent.click(await screen.findByText('新增供应商'))
    const dialog = screen.getByRole('dialog', { name: '新增供应商' })
    fireEvent.change(within(dialog).getByPlaceholderText('my-provider'), { target: { value: 'jd-ds' } })
    expect(within(dialog).getByPlaceholderText('环境变量名')).toHaveValue('JD_DS_API_KEY')
    fireEvent.change(within(dialog).getByPlaceholderText('My Provider'), { target: { value: 'jd-ds' } })
    fireEvent.change(within(dialog).getByPlaceholderText('https://api.openai.com/v1'), { target: { value: 'https://modelservice.jdcloud.com/v1' } })
    fireEvent.click(within(dialog).getByRole('combobox', { name: '能力' }))
    fireEvent.click(within(dialog).getByRole('option', { name: 'embedding' }))
    fireEvent.click(within(dialog).getByText('保存'))

    await waitFor(() => expect(providersApi.create).toHaveBeenCalledWith({
      slug: 'jd-ds', name: 'jd-ds', provider_type: 'openai', base_url: 'https://modelservice.jdcloud.com/v1',
      api_key_env: 'JD_DS_API_KEY', models_endpoint: '/models', capabilities: ['chat', 'embedding'], api_key: undefined,
    }))
    // 新建后直接进入「管理模型」
    expect(await screen.findByRole('dialog', { name: '管理模型 jd-ds' })).toBeInTheDocument()
  })

  it('validates the provider id', async () => {
    render(<ModelServicesPanel />)

    fireEvent.click(await screen.findByText('新增供应商'))
    const dialog = screen.getByRole('dialog', { name: '新增供应商' })
    fireEvent.change(within(dialog).getByPlaceholderText('my-provider'), { target: { value: 'Bad ID' } })
    fireEvent.click(within(dialog).getByText('保存'))

    expect(await within(dialog).findByText(/Provider ID 只能/)).toBeInTheDocument()
    expect(providersApi.create).not.toHaveBeenCalled()
  })

  it('switching provider type offers that type’s capabilities', async () => {
    render(<ModelServicesPanel />)

    fireEvent.click(await screen.findByText('新增供应商'))
    const dialog = screen.getByRole('dialog', { name: '新增供应商' })
    fireEvent.change(within(dialog).getByDisplayValue('OpenAI Completions API'), { target: { value: 'volcengine-ark' } })
    fireEvent.click(within(dialog).getByRole('combobox', { name: '能力' }))
    expect(within(within(dialog).getByRole('listbox')).getAllByRole('option').map((option) => option.textContent)).toEqual(['chat', 'embedding', 'image', 'video'])
  })

  it('edits without changing provider id and saves a new key separately', async () => {
    vi.mocked(providersApi.update).mockResolvedValue(ark)
    render(<ModelServicesPanel />)

    fireEvent.click(await screen.findByLabelText('编辑 Volcengine Ark'))
    const dialog = screen.getByRole('dialog', { name: '编辑供应商' })
    expect(within(dialog).getByDisplayValue('volcengine-ark')).toBeDisabled()
    fireEvent.change(within(dialog).getByPlaceholderText('未配置，填写后保存'), { target: { value: 'secret' } })
    fireEvent.click(within(dialog).getByText('保存'))

    await waitFor(() => expect(providersApi.credential).toHaveBeenCalledWith('volcengine-ark', 'secret'))
    expect(vi.mocked(providersApi.update).mock.calls[0][1]).toEqual({
      name: 'Volcengine Ark', base_url: 'https://ark.cn-beijing.volces.com/api/v3', api_key_env: 'VOLCANO_API_KEY',
      models_endpoint: '/models', capabilities: ['chat', 'video'],
    })
  })
})

describe('manage models', () => {
  it('fetches the model list and adds one by capability', async () => {
    vi.mocked(providersApi.remoteModels).mockResolvedValue([
      { model: 'Qwen/Qwen3-32B', capability: 'chat', added: false },
      { model: 'BAAI/bge-m3', capability: 'embedding', added: true },
    ])
    vi.mocked(providersApi.addModel).mockResolvedValue(siliconflow)
    render(<ModelServicesPanel />)

    fireEvent.click(within(await screen.findByRole('article', { name: 'SiliconFlow' })).getByText(/管理模型/))
    const dialog = screen.getByRole('dialog', { name: '管理模型 SiliconFlow' })
    fireEvent.click(within(dialog).getByText('获取模型列表'))
    expect(await within(dialog).findByText('BAAI/bge-m3')).toBeInTheDocument()
    expect(within(dialog).getByText('已添加')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: '添加' }))
    fireEvent.change(screen.getByLabelText('输入价（元 / 百万 Token）'), { target: { value: '1' } })
    fireEvent.click(within(screen.getByRole('dialog', { name: '添加模型' })).getByText('保存'))

    await waitFor(() => expect(providersApi.addModel).toHaveBeenCalledWith('siliconflow-cn', expect.objectContaining({
      capability: 'chat', model: 'Qwen/Qwen3-32B', pricing: { currency: 'CNY', input_per_million_tokens: '1' },
    })))
  })
})

describe('ModelDialog', () => {
  it('video models keep explicit generation modes and price', async () => {
    const onSaved = vi.fn()
    render(<ModelDialog provider={ark} initial={{ capability: 'video', model: 'doubao-seedance-1-5-pro-251215' }} onClose={vi.fn()} onSaved={onSaved} onError={vi.fn()} />)

    expect(screen.getByLabelText('首尾帧')).toBeChecked()
    fireEvent.click(screen.getByLabelText('首尾帧'))
    fireEvent.change(screen.getByLabelText('单价'), { target: { value: '15' } })
    fireEvent.click(screen.getByText('保存'))

    await waitFor(() => expect(onSaved).toHaveBeenCalled())
    expect(providersApi.addModel).toHaveBeenCalledWith('volcengine-ark', expect.objectContaining({
      capability: 'video', capabilities: { modes: ['text', 'first_frame'] },
    }))
  })

  it('editing sends only model fields', async () => {
    render(<ModelDialog provider={ark} editing={seedance} onClose={vi.fn()} onSaved={vi.fn()} onError={vi.fn()} />)

    fireEvent.change(screen.getByLabelText('单价'), { target: { value: '12' } })
    fireEvent.click(screen.getByText('保存'))

    await waitFor(() => expect(modelServicesApi.update).toHaveBeenCalled())
    const [slug, body] = vi.mocked(modelServicesApi.update).mock.calls[0]
    expect(slug).toBe('ark-seedance')
    expect(Object.keys(body).sort()).toEqual(['capabilities', 'config', 'model', 'name', 'pricing'])
  })
})

describe('DefaultModels', () => {
  const purposes: PurposeDefinition[] = [{ purpose: 'shot_video', modality: 'video', required_capabilities: [] }]
  const routes: ModelRoute[] = [{ purpose: 'shot_video', tier: 'final', modality: 'video', primary_deployment: 'ark-seedance', fallback_deployments: [], enabled: true, revision: 1 }]

  it('shows preview and final for video and saves a tier', async () => {
    vi.mocked(modelServicesApi.saveRoute).mockResolvedValue(routes[0])
    render(<DefaultModels providers={[ark]} purposes={purposes} routes={routes} onChanged={vi.fn().mockResolvedValue(undefined)} notify={vi.fn()} />)

    expect(screen.getByLabelText('镜头视频 · 终版 模型')).toHaveValue('ark-seedance')
    fireEvent.change(screen.getByLabelText('镜头视频 · 预览 模型'), { target: { value: 'ark-seedance' } })

    await waitFor(() => expect(modelServicesApi.saveRoute).toHaveBeenCalledWith(expect.objectContaining({ tier: 'preview', primary_deployment: 'ark-seedance' })))
  })
})
