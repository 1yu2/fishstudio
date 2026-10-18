import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ModelDeployment, ModelRoute, PurposeDefinition } from '../../modelServicesApi'
import { modelServicesApi } from '../../modelServicesApi'
import ModelForm from './ModelForm'
import RoutingMatrix from './RoutingMatrix'
import { VENDOR_PRESETS, customVendor, envNameFor, groupByVendor, priceLabel, slugFor, vendorFor } from './vendors'

vi.mock('../../modelServicesApi', () => ({
  modelServicesApi: {
    create: vi.fn(),
    update: vi.fn(),
    credential: vi.fn(),
    saveRoute: vi.fn(),
    removeRoute: vi.fn(),
  },
}))

function deployment(overrides: Partial<ModelDeployment>): ModelDeployment {
  return {
    id: overrides.slug || 'id', name: overrides.slug || 'model', slug: 'model', driver: 'openai_compatible', modality: 'chat',
    model: 'm', base_url: '', credential_configured: true, enabled: true, priority: 100,
    config: {}, capabilities: {}, pricing: {}, revision: 1, ...overrides,
  }
}

const ark = VENDOR_PRESETS.find((vendor) => vendor.id === 'volcano-ark')!

beforeEach(() => { vi.clearAllMocks() })

describe('vendors', () => {
  it('groups deployments by vendor using host and driver', () => {
    const groups = groupByVendor([
      deployment({ slug: 'seedance', driver: 'ark_video', modality: 'video', base_url: 'https://ark.cn-beijing.volces.com/api/v3' }),
      deployment({ slug: 'doubao', driver: 'openai_compatible', base_url: 'https://ark.cn-beijing.volces.com/api/v3' }),
      deployment({ slug: 'h3', driver: 'local_h3_gateway', modality: 'video', base_url: 'http://192.168.10.60:8088' }),
      deployment({ slug: 'deepseek', driver: 'openai_compatible', base_url: 'https://api.deepseek.com/v1' }),
      deployment({ slug: 'qwen', driver: 'openai_compatible', base_url: 'https://api.siliconflow.cn/v1' }),
    ])

    expect(groups.map((group) => group.vendor.id)).toEqual(['volcano-ark', 'local-h3', 'siliconflow', 'custom:api.deepseek.com'])
    expect(groups[0].items.map((item) => item.slug)).toEqual(['doubao', 'seedance'])
  })

  it('gives every custom OpenAI-compatible vendor its own key variable', () => {
    const deepseek = customVendor('https://api.deepseek.com/v1', 'DeepSeek')
    const kimi = customVendor('https://api.moonshot.cn/v1')

    expect(deepseek.credentialRef).toBe('env:FS_API_DEEPSEEK_COM_API_KEY')
    expect(kimi.credentialRef).toBe('env:FS_API_MOONSHOT_CN_API_KEY')
    expect(deepseek.name).toBe('DeepSeek')
    expect(vendorFor({ driver: 'openai_compatible', base_url: 'https://api.deepseek.com/v1' }).credentialRef).toBe(deepseek.credentialRef)
    expect(envNameFor('---')).toBe('FS_CUSTOM_API_KEY')
  })

  it('builds stable slugs', () => {
    expect(slugFor(ark, 'doubao-seedance-1-5-pro-251215')).toBe('volcano-ark-doubao-seedance-1-5-pro-251215')
    expect(slugFor(customVendor('https://api.deepseek.com/v1'), 'deepseek-chat')).toBe('deepseek-deepseek-chat')
  })

  it('describes pricing in one line', () => {
    expect(priceLabel({ output_per_million_tokens: '15' })).toBe('¥15 / 百万 Token')
    expect(priceLabel({ output_per_second: '0.5' })).toBe('¥0.5 / 秒')
    expect(priceLabel({ per_piece_6: '2', per_piece_10: '4' })).toBe('6s ¥2 · 10s ¥4')
    expect(priceLabel({ output_per_second: '' })).toBe('未定价')
  })
})

const purposes: PurposeDefinition[] = [{ purpose: 'shot_video', modality: 'video', required_capabilities: [] }]
const videoDeployments = [
  deployment({ slug: 'seedance', name: 'Seedance', driver: 'ark_video', modality: 'video', pricing: { output_per_million_tokens: '15' } }),
  deployment({ slug: 'local-h3', name: '本地 H3', driver: 'local_h3_gateway', modality: 'video', pricing: { gpu_per_second: '0.0001' } }),
]
const finalRoute: ModelRoute = {
  purpose: 'shot_video', tier: 'final', modality: 'video', primary_deployment: 'seedance', fallback_deployments: [], enabled: true, revision: 1,
}

describe('RoutingMatrix', () => {
  beforeEach(() => { vi.mocked(modelServicesApi.saveRoute).mockResolvedValue(finalRoute) })

  it('shows inherited tiers and saves a tier-specific binding', async () => {
    const onChanged = vi.fn().mockResolvedValue(undefined)
    render(<RoutingMatrix purposes={purposes} routes={[finalRoute]} deployments={videoDeployments} onChanged={onChanged} notify={vi.fn()} />)

    expect(screen.getAllByText('→ Seedance')).toHaveLength(2)
    expect(screen.getByText('¥15 / 百万 Token')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('镜头视频 预览 主模型'), { target: { value: 'local-h3' } })

    await waitFor(() => expect(modelServicesApi.saveRoute).toHaveBeenCalledWith({
      purpose: 'shot_video', tier: 'preview', modality: 'video', primary_deployment: 'local-h3', fallback_deployments: [], enabled: true,
    }))
    expect(onChanged).toHaveBeenCalled()
  })

  it('clears a preview binding so it falls back to final', async () => {
    const preview = { ...finalRoute, tier: 'preview' as const, primary_deployment: 'local-h3' }
    render(<RoutingMatrix purposes={purposes} routes={[finalRoute, preview]} deployments={videoDeployments} onChanged={vi.fn().mockResolvedValue(undefined)} notify={vi.fn()} />)

    fireEvent.click(screen.getByLabelText('清除 镜头视频 预览'))

    await waitFor(() => expect(modelServicesApi.removeRoute).toHaveBeenCalledWith('shot_video', 'preview'))
  })

  it('adds a fallback without duplicating the primary', async () => {
    render(<RoutingMatrix purposes={purposes} routes={[finalRoute]} deployments={videoDeployments} onChanged={vi.fn().mockResolvedValue(undefined)} notify={vi.fn()} />)

    fireEvent.change(screen.getByLabelText('镜头视频 终版 备用 1'), { target: { value: 'local-h3' } })

    await waitFor(() => expect(modelServicesApi.saveRoute).toHaveBeenCalledWith(expect.objectContaining({
      tier: 'final', primary_deployment: 'seedance', fallback_deployments: ['local-h3'],
    })))
  })
})

describe('ModelForm', () => {
  beforeEach(() => {
    vi.mocked(modelServicesApi.create).mockImplementation(async (body) => deployment({ ...(body as Partial<ModelDeployment>) }))
    vi.mocked(modelServicesApi.update).mockImplementation(async (slug) => deployment({ slug, name: 'Seedance' }))
  })

  it('creates a model under the vendor with its credential and price', async () => {
    const onSaved = vi.fn()
    render(<ModelForm vendor={ark} onSaved={onSaved} onCancel={vi.fn()} onError={vi.fn()} />)

    fireEvent.change(screen.getByLabelText('单价'), { target: { value: '15' } })
    fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'ark-secret' } })
    fireEvent.click(screen.getByText('保存'))

    await waitFor(() => expect(onSaved).toHaveBeenCalled())
    expect(modelServicesApi.create).toHaveBeenCalledWith(expect.objectContaining({
      driver: 'ark_video',
      modality: 'video',
      model: 'doubao-seedance-1-5-pro-251215',
      base_url: 'https://ark.cn-beijing.volces.com/api/v3',
      credential_ref: 'env:VOLCANO_API_KEY',
      slug: 'volcano-ark-doubao-seedance-1-5-pro-251215',
      pricing: { currency: 'CNY', output_per_million_tokens: '15' },
    }))
    expect(modelServicesApi.credential).toHaveBeenCalledWith('volcano-ark-doubao-seedance-1-5-pro-251215', 'ark-secret')
  })

  it('editing never sends driver or credential_ref', async () => {
    const existing = deployment({ slug: 'seedance', driver: 'ark_video', modality: 'video', model: 'm1', pricing: { output_per_million_tokens: '15', per_piece_6: '1' } })
    render(<ModelForm vendor={ark} editing={existing} onSaved={vi.fn()} onCancel={vi.fn()} onError={vi.fn()} />)

    fireEvent.change(screen.getByLabelText('单价'), { target: { value: '12' } })
    fireEvent.click(screen.getByText('保存'))

    await waitFor(() => expect(modelServicesApi.update).toHaveBeenCalled())
    const [slug, body] = vi.mocked(modelServicesApi.update).mock.calls[0]
    expect(slug).toBe('seedance')
    expect(body).not.toHaveProperty('credential_ref')
    expect(body).not.toHaveProperty('driver')
    expect(body.pricing).toEqual({ currency: 'CNY', per_piece_6: '1', output_per_million_tokens: '12' })
    expect(modelServicesApi.credential).not.toHaveBeenCalled()
  })

  it('chat models take input and output token prices', async () => {
    const vendor = customVendor('https://api.deepseek.com/v1', 'DeepSeek')
    render(<ModelForm vendor={vendor} onSaved={vi.fn()} onCancel={vi.fn()} onError={vi.fn()} />)

    fireEvent.change(screen.getByLabelText('模型 ID'), { target: { value: 'deepseek-chat' } })
    fireEvent.change(screen.getByLabelText('输入价（元 / 百万 Token）'), { target: { value: '2' } })
    fireEvent.change(screen.getByLabelText('输出价（元 / 百万 Token）'), { target: { value: '8' } })
    fireEvent.click(screen.getByText('保存'))

    await waitFor(() => expect(modelServicesApi.create).toHaveBeenCalled())
    expect(vi.mocked(modelServicesApi.create).mock.calls[0][0]).toEqual(expect.objectContaining({
      credential_ref: 'env:FS_API_DEEPSEEK_COM_API_KEY',
      pricing: { currency: 'CNY', input_per_million_tokens: '2', output_per_million_tokens: '8' },
    }))
  })

  it('reports invalid advanced JSON instead of saving', async () => {
    const onError = vi.fn()
    render(<ModelForm vendor={ark} onSaved={vi.fn()} onCancel={vi.fn()} onError={onError} />)

    fireEvent.change(screen.getByLabelText('配置'), { target: { value: '{bad' } })
    fireEvent.click(screen.getByText('保存'))

    await waitFor(() => expect(onError).toHaveBeenCalledWith('配置 不是合法的 JSON 对象'))
    expect(modelServicesApi.create).not.toHaveBeenCalled()
  })
})
