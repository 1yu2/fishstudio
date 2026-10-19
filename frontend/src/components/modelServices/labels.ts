/** 接入类型、计价单位、能力标签与用途名称的展示文案。 */

export interface DriverInfo {
  label: string
  modality: string
  defaultModel: string
  priceUnit: PriceUnit
}

export const DRIVERS: Record<string, DriverInfo> = {
  ark_video: { label: 'Seedance 视频', modality: 'video', defaultModel: 'doubao-seedance-1-5-pro-251215', priceUnit: 'output_per_million_tokens' },
  volcano_image: { label: 'Seedream 图片', modality: 'image', defaultModel: 'seedream-4.5', priceUnit: 'output_per_image' },
  volcano_video: { label: '火山视频（旧版 / DMXAPI）', modality: 'video', defaultModel: 'doubao-seedance-1-5-pro', priceUnit: 'output_per_second' },
  openai_compatible: { label: '对话（OpenAI 兼容）', modality: 'chat', defaultModel: '', priceUnit: 'input_per_million_tokens' },
  minimax_video: { label: '海螺视频', modality: 'video', defaultModel: 'MiniMax-Hailuo-02', priceUnit: 'output_per_second' },
  local_h3_gateway: { label: 'H3 视频', modality: 'video', defaultModel: 'MiniMax-H3', priceUnit: 'gpu_per_second' },
  qwen_tts: { label: 'Qwen 语音', modality: 'speech', defaultModel: 'qwen-tts', priceUnit: 'output_per_second' },
  tencent_ai3d: { label: '混元 3D', modality: 'model_3d', defaultModel: 'hunyuan-3d', priceUnit: 'output_per_piece' },
}

export const CAPABILITY_ORDER = ['chat', 'embedding', 'rerank', 'asr', 'tts', 'image', 'video', '3d'] as const

/** 新建模型时价格输入的默认单位 */
export const CAPABILITY_PRICE_UNIT: Record<string, PriceUnit> = {
  chat: 'input_per_million_tokens',
  embedding: 'input_per_million_tokens',
  rerank: 'input_per_million_tokens',
  asr: 'output_per_second',
  tts: 'output_per_second',
  image: 'output_per_image',
  video: 'output_per_second',
  '3d': 'output_per_piece',
}

/** 供应商类型的图标：取首字母，按类型着色 */
export const TYPE_BADGES: Record<string, { text: string; color: string }> = {
  openai: { text: 'AI', color: '#111827' },
  'volcengine-ark': { text: 'Ark', color: '#2563eb' },
  dashscope: { text: 'DS', color: '#6d28d9' },
  minimax: { text: 'MM', color: '#e11d48' },
  'comfyui-gateway': { text: 'H3', color: '#059669' },
  dmxapi: { text: 'DMX', color: '#d97706' },
  'tencent-hunyuan-3d': { text: '3D', color: '#0891b2' },
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

function formatYuan(value: unknown): string {
  const amount = Number(value)
  if (!Number.isFinite(amount)) return String(value)
  return `¥${amount.toLocaleString('zh-CN', { maximumFractionDigits: 6 })}`
}

function filled(pricing: Record<string, unknown>, key: string): boolean {
  return pricing[key] !== undefined && pricing[key] !== null && String(pricing[key]).trim() !== ''
}

/** 价格的一行说明；未配置价格时为「未定价」（这类视频模型不会被路由选中）。 */
export function priceLabel(pricing: Record<string, unknown>, modality?: string): string {
  if (modality === 'chat' && filled(pricing, 'input_per_million_tokens') && filled(pricing, 'output_per_million_tokens')) {
    return `${formatYuan(pricing.input_per_million_tokens)} / ${formatYuan(pricing.output_per_million_tokens)} 每百万 Token`
  }
  if (filled(pricing, 'output_per_million_tokens')) return `${formatYuan(pricing.output_per_million_tokens)} / 百万 Token`
  if (filled(pricing, 'gpu_per_second')) return `${formatYuan(pricing.gpu_per_second)} / GPU 秒`
  if (filled(pricing, 'output_per_second')) return `${formatYuan(pricing.output_per_second)} / 秒`
  if (filled(pricing, 'output_per_image')) return `${formatYuan(pricing.output_per_image)} / 张`
  const pieces = Object.keys(pricing).filter((key) => key.startsWith('per_piece_') && filled(pricing, key))
  if (pieces.length) return pieces.map((key) => `${key.replace('per_piece_', '')}s ${formatYuan(pricing[key])}`).join(' · ')
  if (filled(pricing, 'output_per_piece')) return `${formatYuan(pricing.output_per_piece)} / 个`
  return '未定价'
}

/** 从能力配置里提炼给人看的标签，例如「首帧」「工具」。 */
export function capabilityTags(capabilities: Record<string, unknown>): string[] {
  const tags: string[] = []
  const modes = Array.isArray(capabilities.modes) ? capabilities.modes as string[] : []
  if (modes.includes('first_frame')) tags.push('首帧')
  if (modes.includes('first_last_frame')) tags.push('首尾帧')
  if (modes.includes('reference')) tags.push('参考图')
  const operations = Array.isArray(capabilities.operations) ? capabilities.operations as string[] : []
  if (operations.includes('edit')) tags.push('图片编辑')
  if (capabilities.tools === true) tags.push('工具')
  if (capabilities.vision === true) tags.push('视觉')
  return tags
}

/** 与后端 routes._supports 相同的判断：能力值为 true 或出现在 operations 中。 */
export function supportsCapabilities(capabilities: Record<string, unknown>, required: string[]): boolean {
  const operations = Array.isArray(capabilities.operations) ? capabilities.operations as string[] : []
  return required.every((capability) => capabilities[capability] === true || operations.includes(capability))
}

export const PURPOSE_LABELS: Record<string, { label: string; hint: string }> = {
  chat: { label: '对话 / Copilot', hint: '对话助手与自由创作' },
  planner: { label: '剧本拆分与规划', hint: '需要支持工具调用' },
  director: { label: '导演 / 提示词', hint: '把镜头描述编译成生成提示词' },
  reviewer: { label: '看图审片', hint: '需要支持视觉' },
  image_generation: { label: '图片生成', hint: '定妆图、场景图、关键帧' },
  image_edit: { label: '图片编辑', hint: '局部修改、换装' },
  shot_video: { label: '镜头视频', hint: '预览可用便宜或自建模型，终版用效果更好的模型' },
  speech: { label: '配音', hint: '对白与旁白' },
  model_3d: { label: '3D 模型', hint: '' },
}

export const TIER_LABELS = { draft: '草稿', preview: '预览', final: '终版' } as const
