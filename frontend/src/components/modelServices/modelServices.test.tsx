import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ModelProvider, ModelRoute, ProviderModel, ProviderPreset, PurposeDefinition } from '../../modelServicesApi'
import { modelServicesApi, providersApi } from '../../modelServicesApi'
import DefaultModels from './DefaultModels'
import ModelDialog from './ModelDialog'
import ModelServicesPanel from './ModelServicesPanel'
import { capabilityTags, priceLabel, supportsCapabilities } from './labels'

vi.mock('../../modelServicesApi', () => ({
  providersApi: {
    list: vi.fn(), presets: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn(),
    credential: vi.fn(), check: vi.fn(), remoteModels: vi.fn(), addModel: vi.fn(),
  },
  modelServicesApi: {
    routes: vi.fn(), purposes: vi.fn(), runtime: vi.fn(), saveRoute: vi.fn(), removeRoute: vi.fn(),
    update: vi.fn(), enable: vi.fn(), remove: vi.fn(),
  },
}))

const arkPreset: ProviderPreset = {
  id: 'volcano-ark', name: '火山方舟', description: 'Seedance 视频', drivers: ['ark_video', 'volcano_image', 'openai_compatible'],
  default_base_url: 'https://ark.cn-beijing.volces.com/api/v3', check: 'ark', lists_models: true, self_hosted: false, custom: false, tags: ['视频'],
}
const deepseekPreset: ProviderPreset = { ...arkPreset, id: 'deepseek', name: 'DeepSeek', drivers: ['openai_compatible'], default_base_url: 'https://api.deepseek.com/v1', tags: ['对话'] }
const customPreset: ProviderPreset = { ...deepseekPreset, id: 'openai-compatible', name: '自定义 OpenAI 兼容', default_base_url: '', custom: true }

function model(overrides: Partial<ProviderModel>): ProviderModel {
  return {
    id: 'm', name: 'm', slug: 'm', driver: 'ark_video', modality: 'video', model: 'm', base_url: '', credential_configured: true,
    enabled: true, priority: 100, config: {}, capabilities: {}, pricing: {}, revision: 1, available: true, ...overrides,
  }
}

const seedance = model({ slug: 'volcano-ark-seedance', name: 'Seedance 1.5 Pro', model: 'doubao-seedance-1-5-pro', capabilities: { modes: ['text', 'first_frame'] }, pricing: { output_per_million_tokens: '15' } })
const ark: ModelProvider = { id: 'p1', slug: 'volcano-ark', name: '火山方舟', preset: arkPreset, base_url: arkPreset.default_base_url, credential_configured: false, enabled: true, revision: 1, models: [seedance] }

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(providersApi.list).mockResolvedValue([ark])
  vi.mocked(providersApi.presets).mockResolvedValue([arkPreset, deepseekPreset, customPreset])
  vi.mocked(modelServicesApi.routes).mockResolvedValue([])
  vi.mocked(modelServicesApi.purposes).mockResolvedValue([])
  vi.mocked(modelServicesApi.runtime).mockResolvedValue({ items: [] })
})

describe('labels', () => {
  it('describes capabilities and prices', () => {
    expect(capabilityTags({ modes: ['text', 'first_frame', 'first_last_frame'], tools: true })).toEqual(['首帧', '首尾帧', '工具'])
    expect(priceLabel({ input_per_million_tokens: '2', output_per_million_tokens: '8' }, 'chat')).toBe('¥2 / ¥8 每百万 Token')
    expect(priceLabel({ gpu_per_second: '0.0001' })).toBe('¥0.0001 / GPU 秒')
    expect(priceLabel({})).toBe('未定价')
    expect(supportsCapabilities({ operations: ['generate'] }, ['generate'])).toBe(true)
    expect(supportsCapabilities({ tools: false }, ['tools'])).toBe(false)
  })
})

describe('ModelServicesPanel', () => {
  it('lists providers and shows the selected provider with grouped models', async () => {
    render(<ModelServicesPanel />)

    const list = await screen.findByLabelText('供应商列表')
    expect(within(list).getByText('火山方舟')).toBeInTheDocument()
    expect(within(list).getByRole('button', { name: '火山方舟，1 个模型，缺少密钥' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '火山方舟' })).toBeInTheDocument()
    expect(screen.getByText('Seedance 1.5 Pro')).toBeInTheDocument()
    expect(screen.getByText('首帧')).toBeInTheDocument()
    expect(screen.getByText('¥15 / 百万 Token')).toBeInTheDocument()
  })

  it('saves the key before checking the connection', async () => {
    vi.mocked(providersApi.check).mockResolvedValue({ ok: true, message: '连接正常' })
    render(<ModelServicesPanel />)

    fireEvent.change(await screen.findByLabelText('API 密钥'), { target: { value: 'ark-secret' } })
    fireEvent.click(screen.getByRole('button', { name: '检查' }))

    expect(await screen.findByText('连接正常')).toBeInTheDocument()
    expect(providersApi.credential).toHaveBeenCalledWith('volcano-ark', 'ark-secret')
    expect(vi.mocked(providersApi.credential).mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(providersApi.check).mock.invocationCallOrder[0])
  })

  it('adds a preset provider and a custom OpenAI-compatible one', async () => {
    vi.mocked(providersApi.create).mockImplementation(async (body) => ({ ...ark, slug: body.preset, name: body.name || 'DeepSeek', models: [] }))
    render(<ModelServicesPanel />)

    fireEvent.click(await screen.findByText('添加供应商'))
    // 已添加的火山方舟：点击只跳转到已有供应商，不新建
    fireEvent.click(within(screen.getByRole('dialog', { name: '添加供应商' })).getByText('火山方舟'))
    expect(screen.queryByRole('dialog', { name: '添加供应商' })).not.toBeInTheDocument()
    expect(providersApi.create).not.toHaveBeenCalled()

    fireEvent.click(screen.getByText('添加供应商'))
    fireEvent.click(within(screen.getByRole('dialog', { name: '添加供应商' })).getByText('DeepSeek'))
    await waitFor(() => expect(providersApi.create).toHaveBeenCalledWith({ preset: 'deepseek', name: '', base_url: '' }))

    fireEvent.click(await screen.findByText('添加供应商'))
    const dialog = screen.getByRole('dialog', { name: '添加供应商' })
    fireEvent.change(within(dialog).getByPlaceholderText('名称，例如 公司网关'), { target: { value: 'vLLM' } })
    fireEvent.change(within(dialog).getByPlaceholderText('https://api.example.com/v1'), { target: { value: 'http://10.0.0.5:8000/v1' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '添加' }))
    await waitFor(() => expect(providersApi.create).toHaveBeenLastCalledWith({ preset: 'openai-compatible', name: 'vLLM', base_url: 'http://10.0.0.5:8000/v1' }))
  })

  it('toggles a model and the provider', async () => {
    render(<ModelServicesPanel />)

    fireEvent.click(await screen.findByRole('switch', { name: '启用 Seedance 1.5 Pro' }))
    await waitFor(() => expect(modelServicesApi.enable).toHaveBeenCalledWith('volcano-ark-seedance', false))
    fireEvent.click(screen.getByRole('switch', { name: '启用 火山方舟' }))
    await waitFor(() => expect(providersApi.update).toHaveBeenCalledWith('volcano-ark', { enabled: false }))
  })
})

describe('ModelDialog', () => {
  it('adds a model under the provider with modes and price', async () => {
    const onSaved = vi.fn()
    render(<ModelDialog provider={ark} onClose={vi.fn()} onSaved={onSaved} onError={vi.fn()} />)

    // Seedance 默认三种方式全部勾选；取消首尾帧后按界面所见保存
    expect(screen.getByLabelText('首尾帧')).toBeChecked()
    fireEvent.click(screen.getByLabelText('首尾帧'))
    fireEvent.change(screen.getByLabelText('单价'), { target: { value: '15' } })
    fireEvent.click(screen.getByText('保存'))

    await waitFor(() => expect(onSaved).toHaveBeenCalled())
    expect(providersApi.addModel).toHaveBeenCalledWith('volcano-ark', {
      driver: 'ark_video',
      name: 'doubao-seedance-1-5-pro-251215',
      model: 'doubao-seedance-1-5-pro-251215',
      config: {},
      capabilities: { modes: ['text', 'first_frame'] },
      pricing: { currency: 'CNY', output_per_million_tokens: '15' },
    })
  })

  it('editing sends only model fields', async () => {
    render(<ModelDialog provider={ark} editing={seedance} onClose={vi.fn()} onSaved={vi.fn()} onError={vi.fn()} />)

    fireEvent.change(screen.getByLabelText('单价'), { target: { value: '12' } })
    fireEvent.click(screen.getByText('保存'))

    await waitFor(() => expect(modelServicesApi.update).toHaveBeenCalled())
    const [slug, body] = vi.mocked(modelServicesApi.update).mock.calls[0]
    expect(slug).toBe('volcano-ark-seedance')
    expect(Object.keys(body).sort()).toEqual(['capabilities', 'config', 'model', 'name', 'pricing'])
    expect(body.pricing).toEqual({ currency: 'CNY', output_per_million_tokens: '12' })
  })

  it('chat models take token prices and capability flags', async () => {
    const deepseek: ModelProvider = { ...ark, slug: 'deepseek', name: 'DeepSeek', preset: deepseekPreset, models: [] }
    render(<ModelDialog provider={deepseek} initial={{ driver: 'openai_compatible', model: 'deepseek-chat' }} onClose={vi.fn()} onSaved={vi.fn()} onError={vi.fn()} />)

    fireEvent.click(screen.getByLabelText('工具调用'))
    fireEvent.change(screen.getByLabelText('输入价（元 / 百万 Token）'), { target: { value: '2' } })
    fireEvent.change(screen.getByLabelText('输出价（元 / 百万 Token）'), { target: { value: '8' } })
    fireEvent.click(screen.getByText('保存'))

    await waitFor(() => expect(providersApi.addModel).toHaveBeenCalled())
    expect(vi.mocked(providersApi.addModel).mock.calls[0][1]).toEqual(expect.objectContaining({
      driver: 'openai_compatible', model: 'deepseek-chat', capabilities: { tools: true },
      pricing: { currency: 'CNY', input_per_million_tokens: '2', output_per_million_tokens: '8' },
    }))
  })
})

describe('second account of a preset', () => {
  it('is created only through the explicit button', async () => {
    vi.mocked(providersApi.create).mockResolvedValue({ ...ark, slug: 'volcano-ark-2', models: [] })
    render(<ModelServicesPanel />)

    fireEvent.click(await screen.findByText('添加供应商'))
    fireEvent.click(within(screen.getByRole('dialog', { name: '添加供应商' })).getByText('再添加一个账号'))

    await waitFor(() => expect(providersApi.create).toHaveBeenCalledWith({ preset: 'volcano-ark', name: '', base_url: '' }))
  })
})

describe('DefaultModels', () => {
  const purposes: PurposeDefinition[] = [
    { purpose: 'shot_video', modality: 'video', required_capabilities: [] },
    { purpose: 'planner', modality: 'chat', required_capabilities: ['tools'] },
  ]
  const deepseek: ModelProvider = {
    ...ark, slug: 'deepseek', name: 'DeepSeek', preset: deepseekPreset, models: [
      model({ slug: 'ds-chat', name: 'deepseek-chat', driver: 'openai_compatible', modality: 'chat', capabilities: { tools: true } }),
      model({ slug: 'ds-r1', name: 'deepseek-reasoner', driver: 'openai_compatible', modality: 'chat', capabilities: {} }),
    ],
  }
  const routes: ModelRoute[] = [{ purpose: 'shot_video', tier: 'final', modality: 'video', primary_deployment: 'volcano-ark-seedance', fallback_deployments: [], enabled: true, revision: 1 }]

  it('shows preview and final for video and only capable models', () => {
    render(<DefaultModels providers={[ark, deepseek]} purposes={purposes} routes={routes} onChanged={vi.fn()} notify={vi.fn()} />)

    expect(screen.getByText('镜头视频 · 预览')).toBeInTheDocument()
    expect(screen.getByText('未单独设置，沿用终版')).toBeInTheDocument()
    expect(screen.getByLabelText('镜头视频 · 终版 模型')).toHaveValue('volcano-ark-seedance')
    const planner = screen.getByLabelText('剧本拆分与规划 模型')
    expect(within(planner).getByText('DeepSeek · deepseek-chat')).toBeInTheDocument()
    expect(within(planner).queryByText('DeepSeek · deepseek-reasoner')).not.toBeInTheDocument()
  })

  it('saves a tier-specific default', async () => {
    vi.mocked(modelServicesApi.saveRoute).mockResolvedValue(routes[0])
    const onChanged = vi.fn().mockResolvedValue(undefined)
    render(<DefaultModels providers={[ark]} purposes={purposes} routes={routes} onChanged={onChanged} notify={vi.fn()} />)

    fireEvent.change(screen.getByLabelText('镜头视频 · 预览 模型'), { target: { value: 'volcano-ark-seedance' } })

    await waitFor(() => expect(modelServicesApi.saveRoute).toHaveBeenCalledWith({
      purpose: 'shot_video', tier: 'preview', modality: 'video', primary_deployment: 'volcano-ark-seedance', fallback_deployments: [], enabled: true,
    }))
    expect(onChanged).toHaveBeenCalled()
  })
})
