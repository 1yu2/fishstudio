import { useState } from 'react'
import { ChevronDown, X } from 'lucide-react'
import { Capability, ModelProvider, ProviderModel, modelServicesApi, providersApi } from '../../modelServicesApi'
import { CAPABILITY_PRICE_UNIT, DRIVERS, PRICE_UNITS, PriceUnit } from './labels'

const TOKEN_PRICE_KEYS = ['input_per_million_tokens', 'output_per_million_tokens', 'cached_input_per_million_tokens'] as const
const MANAGED_PRICE_KEYS = new Set<string>([...Object.keys(PRICE_UNITS), ...TOKEN_PRICE_KEYS])
const VIDEO_MODES: [string, string][] = [['text', '文生视频'], ['first_frame', '首帧'], ['first_last_frame', '首尾帧']]
const CHAT_FLAGS: [string, string][] = [['tools', '工具调用'], ['vision', '视觉理解']]
// 这些视频 Driver 的能力里用 modes 声明支持的生成方式
const MODE_DRIVERS = new Set(['ark_video', 'local_h3_gateway', 'minimax_video', 'volcano_video'])

interface Props {
  provider: ModelProvider
  editing?: ProviderModel
  initial?: { capability: Capability; model: string }
  onClose: () => void
  onSaved: (message: string) => Promise<void> | void
  onError: (message: string) => void
}

// 新建视频模型时显式写入默认生成方式，界面勾选与实际保存保持一致。
function defaultCapabilities(driver: string): Record<string, unknown> {
  if (driver === 'ark_video') return { modes: ['text', 'first_frame', 'first_last_frame'] }
  if (MODE_DRIVERS.has(driver)) return { modes: ['text'] }
  return {}
}

function parseObject(label: string, text: string): Record<string, unknown> {
  try {
    const value = JSON.parse(text || '{}')
    if (value && typeof value === 'object' && !Array.isArray(value)) return value
  } catch { /* 下方统一报错 */ }
  throw new Error(`${label} 不是合法的 JSON 对象`)
}

function pick(pricing: Record<string, unknown>, keys: readonly string[]) {
  return Object.fromEntries(keys.map((key) => [key, pricing[key] !== undefined ? String(pricing[key]) : '']))
}

export default function ModelDialog({ provider, editing, initial, onClose, onSaved, onError }: Props) {
  const options = provider.capabilities
  const [capability, setCapability] = useState<Capability>(editing?.capability || initial?.capability || options[0])
  const driver = editing?.driver || provider.provider_type.drivers[capability] || ''
  const tokenPriced = ['chat', 'embedding', 'rerank'].includes(capability)
  const pricing0 = editing?.pricing || {}
  const [model, setModel] = useState(editing?.model ?? initial?.model ?? DRIVERS[driver]?.defaultModel ?? '')
  const [name, setName] = useState(editing?.name || '')
  const [unit, setUnit] = useState<PriceUnit>(() => {
    const stored = (Object.keys(PRICE_UNITS) as PriceUnit[]).find((key) => !key.startsWith('input_') && String(pricing0[key] ?? '').trim())
    const fallback = CAPABILITY_PRICE_UNIT[capability] || 'output_per_second'
    return stored || (fallback.startsWith('input_') ? 'output_per_second' : fallback)
  })
  const [amount, setAmount] = useState(() => String(pricing0[unit] ?? ''))
  const [tokenPrice, setTokenPrice] = useState<Record<string, string>>(() => pick(pricing0, TOKEN_PRICE_KEYS))
  const [capabilities, setCapabilities] = useState(JSON.stringify(editing?.capabilities || defaultCapabilities(driver), null, 2))
  const [config, setConfig] = useState(JSON.stringify(editing?.config || {}, null, 2))
  const [extraPricing, setExtraPricing] = useState(JSON.stringify(
    Object.fromEntries(Object.entries(pricing0).filter(([key]) => !MANAGED_PRICE_KEYS.has(key) && key !== 'currency')), null, 2))
  const [busy, setBusy] = useState(false)

  const capabilityObject = (): Record<string, unknown> => {
    try { return parseObject('能力参数', capabilities) } catch { return {} }
  }
  const modes = (capabilityObject().modes as string[] | undefined) || ['text']
  const toggleFlag = (key: string, on: boolean) => {
    const current = capabilityObject()
    if (VIDEO_MODES.some(([mode]) => mode === key)) {
      const next = on ? [...new Set([...modes, key])] : modes.filter((mode) => mode !== key)
      current.modes = next.length ? next : ['text']
    } else {
      current[key] = on
    }
    setCapabilities(JSON.stringify(current, null, 2))
  }

  const changeCapability = (value: Capability) => {
    setCapability(value)
    const nextDriver = provider.provider_type.drivers[value] || ''
    setModel(DRIVERS[nextDriver]?.defaultModel || '')
    const nextUnit = CAPABILITY_PRICE_UNIT[value] || 'output_per_second'
    setUnit(nextUnit.startsWith('input_') ? 'output_per_second' : nextUnit)
    setCapabilities(JSON.stringify(defaultCapabilities(nextDriver), null, 2))
  }

  const buildPricing = (): Record<string, unknown> => {
    const pricing: Record<string, unknown> = { currency: 'CNY', ...parseObject('其他价格字段', extraPricing) }
    if (tokenPriced) {
      for (const key of TOKEN_PRICE_KEYS) if (tokenPrice[key].trim()) pricing[key] = tokenPrice[key].trim()
    } else if (amount.trim()) {
      pricing[unit] = amount.trim()
    }
    return pricing
  }

  const save = async () => {
    setBusy(true)
    try {
      if (!model.trim()) throw new Error('填写 Model ID')
      const body = {
        name: name.trim() || model.trim(),
        model: model.trim(),
        config: parseObject('配置', config),
        capabilities: parseObject('能力参数', capabilities),
        pricing: buildPricing(),
      }
      if (editing) {
        // 编辑只改模型自身；地址与密钥由供应商统一管理。
        await modelServicesApi.update(editing.slug, body)
      } else {
        await providersApi.addModel(provider.slug, { capability, ...body })
      }
      await onSaved(editing ? `${body.name} 已更新` : `${body.name} 已添加`)
    } catch (error) {
      onError(error instanceof Error ? error.message : '保存失败')
    } finally {
      setBusy(false)
    }
  }

  return <div className="ms-dialog ms-dialog--top" role="dialog" aria-label={editing ? '编辑模型' : '添加模型'}>
    <div className="ms-dialog__panel">
      <div className="ms-dialog__head">
        <strong>{editing ? `编辑 ${editing.name}` : `添加模型 · ${provider.name}`}</strong>
        <button className="ms-icon-btn" onClick={onClose} aria-label="关闭"><X size={16} /></button>
      </div>
      <div className="ms-form">
        <label>能力
          <select className="settings-input" disabled={!!editing} value={capability} onChange={(e) => changeCapability(e.target.value as Capability)}>
            {options.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
        <label>Model ID<input className="settings-input" value={model} placeholder={DRIVERS[driver]?.defaultModel || 'model-id'} onChange={(e) => setModel(e.target.value)} /></label>
        <label className="ms-form__wide">展示名称<input className="settings-input" value={name} placeholder="留空则使用 Model ID" onChange={(e) => setName(e.target.value)} /></label>

        {MODE_DRIVERS.has(driver) && <fieldset className="ms-form__wide ms-checks"><legend>生成方式</legend>
          {VIDEO_MODES.map(([key, label]) => <label key={key} className="ms-check">
            <input type="checkbox" checked={modes.includes(key)} onChange={(e) => toggleFlag(key, e.target.checked)} />{label}
          </label>)}
        </fieldset>}
        {capability === 'chat' && <fieldset className="ms-form__wide ms-checks"><legend>模型能力</legend>
          {CHAT_FLAGS.map(([key, label]) => <label key={key} className="ms-check">
            <input type="checkbox" checked={capabilityObject()[key] === true} onChange={(e) => toggleFlag(key, e.target.checked)} />{label}
          </label>)}
        </fieldset>}

        {tokenPriced ? <>
          <label>输入价（元 / 百万 Token）<input className="settings-input" inputMode="decimal" value={tokenPrice.input_per_million_tokens} onChange={(e) => setTokenPrice({ ...tokenPrice, input_per_million_tokens: e.target.value })} /></label>
          {capability === 'chat' && <label>输出价（元 / 百万 Token）<input className="settings-input" inputMode="decimal" value={tokenPrice.output_per_million_tokens} onChange={(e) => setTokenPrice({ ...tokenPrice, output_per_million_tokens: e.target.value })} /></label>}
        </> : <>
          <label>计价单位
            <select className="settings-input" value={unit} onChange={(e) => setUnit(e.target.value as PriceUnit)}>
              {(Object.keys(PRICE_UNITS) as PriceUnit[]).filter((key) => !key.startsWith('input_')).map((key) => <option key={key} value={key}>{PRICE_UNITS[key]}</option>)}
            </select>
          </label>
          <label>单价<input className="settings-input" inputMode="decimal" value={amount} placeholder="未填写的视频模型不会被选中" onChange={(e) => setAmount(e.target.value)} /></label>
        </>}
      </div>
      <details className="ms-advanced"><summary><ChevronDown size={14} />高级（JSON）</summary>
        <label>能力参数<textarea className="settings-textarea" value={capabilities} onChange={(e) => setCapabilities(e.target.value)} /></label>
        <label>配置<textarea className="settings-textarea" value={config} onChange={(e) => setConfig(e.target.value)} /></label>
        <label>其他价格字段（如按条计费 per_piece_6）<textarea className="settings-textarea" value={extraPricing} onChange={(e) => setExtraPricing(e.target.value)} /></label>
      </details>
      <div className="ms-dialog__foot">
        <button className="model-action" onClick={onClose}>取消</button>
        <button className="settings-save-btn" disabled={busy} onClick={() => void save()}>{busy ? '保存中…' : '保存'}</button>
      </div>
    </div>
  </div>
}
