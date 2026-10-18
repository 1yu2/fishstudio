import type { ModelDeployment } from '../../modelServicesApi'

/** 接入类型（后端 Driver）及新建模型时的默认值。 */
export interface DriverInfo {
  label: string
  modality: string
  defaultModel: string
  /** 价格输入框默认的计价单位 */
  priceUnit: PriceUnit
}

export const DRIVERS: Record<string, DriverInfo> = {
  ark_video: { label: 'Seedance 视频', modality: 'video', defaultModel: 'doubao-seedance-1-5-pro-251215', priceUnit: 'output_per_million_tokens' },
  volcano_image: { label: 'Seedream 图片', modality: 'image', defaultModel: 'seedream-4.5', priceUnit: 'output_per_image' },
  volcano_video: { label: '火山视频（旧版 / DMXAPI）', modality: 'video', defaultModel: 'doubao-seedance-1-5-pro', priceUnit: 'output_per_second' },
  openai_compatible: { label: 'OpenAI 兼容对话', modality: 'chat', defaultModel: '', priceUnit: 'input_per_million_tokens' },
  minimax_video: { label: 'MiniMax 视频', modality: 'video', defaultModel: 'MiniMax-Hailuo-02', priceUnit: 'output_per_second' },
  local_h3_gateway: { label: 'H3 Gateway 视频', modality: 'video', defaultModel: 'MiniMax-H3', priceUnit: 'output_per_second' },
  qwen_tts: { label: 'Qwen 语音', modality: 'speech', defaultModel: 'qwen-tts', priceUnit: 'output_per_second' },
  tencent_ai3d: { label: '混元 3D', modality: 'model_3d', defaultModel: 'hunyuan-3d', priceUnit: 'output_per_piece' },
}

export type PriceUnit =
  | 'output_per_second'
  | 'output_per_million_tokens'
  | 'gpu_per_second'
  | 'output_per_image'
  | 'output_per_piece'
  | 'input_per_million_tokens'

export const PRICE_UNITS: Record<PriceUnit, string> = {
  output_per_second: '元 / 输出秒',
  output_per_million_tokens: '元 / 百万输出 Token',
  gpu_per_second: '元 / GPU 秒（自建）',
  output_per_image: '元 / 张',
  output_per_piece: '元 / 个',
  input_per_million_tokens: '元 / 百万输入 Token',
}

export interface Vendor {
  id: string
  name: string
  description: string
  drivers: string[]
  baseUrl: string
  /** 同一厂商下所有模型共用的密钥引用；密钥本身只写入后端，不回传页面 */
  credentialRef: string
  hosts: string[]
  selfHosted?: boolean
  custom?: boolean
}

export const VENDOR_PRESETS: Vendor[] = [
  {
    id: 'volcano-ark', name: '火山方舟', description: 'Seedance 视频、Seedream 图片、豆包对话',
    drivers: ['ark_video', 'volcano_image', 'openai_compatible', 'volcano_video'],
    baseUrl: 'https://ark.cn-beijing.volces.com/api/v3', credentialRef: 'env:VOLCANO_API_KEY',
    hosts: ['volces.com', 'volcengine.com', 'dmxapi.cn'],
  },
  {
    id: 'local-h3', name: '自建 GPU（H3 Gateway）', description: '本机或局域网 ComfyUI 上的量化 H3',
    drivers: ['local_h3_gateway'], baseUrl: '', credentialRef: 'env:LOCAL_H3_API_KEY', hosts: [], selfHosted: true,
  },
  {
    id: 'minimax', name: 'MiniMax 海螺', description: '海螺视频',
    drivers: ['minimax_video'], baseUrl: 'https://api.minimaxi.com', credentialRef: 'env:MINIMAX_API_KEY',
    hosts: ['minimaxi.com', 'minimax.chat', 'minimax.io'],
  },
  {
    id: 'dashscope', name: '阿里云百炼', description: 'Qwen 语音合成与声音复刻',
    drivers: ['qwen_tts'], baseUrl: 'https://dashscope.aliyuncs.com', credentialRef: 'env:DASHSCOPE_API_KEY',
    hosts: ['aliyuncs.com'],
  },
  {
    id: 'siliconflow', name: '硅基流动', description: 'OpenAI 兼容的开源模型对话',
    drivers: ['openai_compatible'], baseUrl: 'https://api.siliconflow.cn/v1', credentialRef: 'env:OPENAI_API_KEY',
    hosts: ['siliconflow.cn', 'siliconflow.com'],
  },
  {
    id: 'tencent', name: '腾讯混元 3D', description: '文本 / 图片生成 3D 模型',
    drivers: ['tencent_ai3d'], baseUrl: '', credentialRef: 'env:TENCENT_AI3D_API_KEY', hosts: ['tencentcloudapi.com'],
  },
]

const DRIVER_HOME: Record<string, string> = {
  local_h3_gateway: 'local-h3',
  minimax_video: 'minimax',
  qwen_tts: 'dashscope',
  tencent_ai3d: 'tencent',
  ark_video: 'volcano-ark',
  volcano_image: 'volcano-ark',
  volcano_video: 'volcano-ark',
}

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname.toLowerCase()
  } catch {
    return ''
  }
}

function matchesHost(host: string, suffixes: string[]): boolean {
  return suffixes.some((suffix) => host === suffix || host.endsWith(`.${suffix}`))
}

/** 自定义厂商的密钥变量名由服务地址推导，稳定且无需后端额外存储。 */
export function envNameFor(label: string): string {
  const core = label.toUpperCase().replace(/[^A-Z0-9]+/g, '_').replace(/^_+|_+$/g, '')
  return `FS_${core || 'CUSTOM'}_API_KEY`
}

export function customVendor(baseUrl: string, name?: string): Vendor {
  const host = hostOf(baseUrl) || baseUrl
  return {
    id: `custom:${host}`,
    name: name?.trim() || host,
    description: 'OpenAI 兼容接口（DeepSeek、Kimi、OpenRouter、自部署 vLLM 等）',
    drivers: ['openai_compatible'],
    baseUrl,
    credentialRef: `env:${envNameFor(host)}`,
    hosts: [host],
    custom: true,
  }
}

export function vendorFor(item: Pick<ModelDeployment, 'driver' | 'base_url'>): Vendor {
  const host = hostOf(item.base_url)
  const byHost = host ? VENDOR_PRESETS.find((vendor) => vendor.drivers.includes(item.driver) && matchesHost(host, vendor.hosts)) : undefined
  if (byHost) return byHost
  const home = VENDOR_PRESETS.find((vendor) => vendor.id === DRIVER_HOME[item.driver])
  if (home) return home
  return customVendor(item.base_url)
}

export interface VendorGroup {
  vendor: Vendor
  items: ModelDeployment[]
}

/** 按厂商分组：预设厂商按固定顺序在前，自定义厂商按名称在后；组内按用途模态与名称排序。 */
export function groupByVendor(items: ModelDeployment[]): VendorGroup[] {
  const groups = new Map<string, VendorGroup>()
  for (const item of items) {
    const vendor = vendorFor(item)
    const group = groups.get(vendor.id) || { vendor, items: [] }
    group.items.push(item)
    groups.set(vendor.id, group)
  }
  const order = (group: VendorGroup) => {
    const index = VENDOR_PRESETS.findIndex((vendor) => vendor.id === group.vendor.id)
    return index === -1 ? VENDOR_PRESETS.length : index
  }
  return [...groups.values()]
    .map((group) => ({ ...group, items: [...group.items].sort((a, b) => a.modality.localeCompare(b.modality) || a.name.localeCompare(b.name)) }))
    .sort((a, b) => order(a) - order(b) || a.vendor.name.localeCompare(b.vendor.name))
}

export function slugFor(vendor: Vendor, model: string): string {
  const raw = `${vendor.custom ? hostOf(vendor.baseUrl).split('.').slice(-2, -1)[0] || 'custom' : vendor.id}-${model}`
  return raw.toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '').slice(0, 120)
}

function formatYuan(value: unknown): string {
  const amount = Number(value)
  if (!Number.isFinite(amount)) return String(value)
  return `¥${amount.toLocaleString('zh-CN', { maximumFractionDigits: 6 })}`
}

/** 价格的一行说明；未配置价格时提示「未定价」（这类模型不会被视频路由选中）。 */
export function priceLabel(pricing: Record<string, unknown>): string {
  const filled = (key: string) => pricing[key] !== undefined && pricing[key] !== null && String(pricing[key]).trim() !== ''
  if (filled('output_per_million_tokens')) return `${formatYuan(pricing.output_per_million_tokens)} / 百万 Token`
  if (filled('gpu_per_second')) return `${formatYuan(pricing.gpu_per_second)} / GPU 秒`
  if (filled('output_per_second')) return `${formatYuan(pricing.output_per_second)} / 秒`
  if (filled('output_per_image')) return `${formatYuan(pricing.output_per_image)} / 张`
  const pieces = Object.keys(pricing).filter((key) => key.startsWith('per_piece_') && filled(key))
  if (pieces.length) return pieces.map((key) => `${key.replace('per_piece_', '')}s ${formatYuan(pricing[key])}`).join(' · ')
  if (filled('output_per_piece')) return `${formatYuan(pricing.output_per_piece)} / 个`
  if (filled('input_per_million_tokens')) return `${formatYuan(pricing.input_per_million_tokens)} / 百万输入 Token`
  return '未定价'
}

export const PURPOSE_LABELS: Record<string, string> = {
  chat: '对话 Copilot',
  planner: '规划 / 拆剧本',
  director: '导演 / 提示词',
  reviewer: '看图审片',
  image_generation: '图片生成',
  image_edit: '图片编辑',
  shot_video: '镜头视频',
  speech: '配音',
  model_3d: '3D 模型',
}

export const TIER_LABELS = { draft: '草稿', preview: '预览', final: '终版' } as const
