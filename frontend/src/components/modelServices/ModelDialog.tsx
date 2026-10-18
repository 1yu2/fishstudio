import { useState } from 'react'
import { ChevronDown, X } from 'lucide-react'
import { ModelProvider, ProviderModel, modelServicesApi, providersApi } from '../../modelServicesApi'
import { DRIVERS, PRICE_UNITS, PriceUnit } from './labels'

const CHAT_PRICE_KEYS = ['input_per_million_tokens', 'output_per_million_tokens', 'cached_input_per_million_tokens'] as const
const MANAGED_PRICE_KEYS = new Set<string>([...Object.keys(PRICE_UNITS), ...CHAT_PRICE_KEYS])
const VIDEO_MODES: [string, string][] = [['text', '文生视频'], ['first_frame', '首帧'], ['first_last_frame', '首尾帧']]
const CHAT_FLAGS: [string, string][] = [['tools', '工具调用'], ['vision', '视觉理解']]
// 这些视频 Driver 的能力里用 modes 声明支持的生成方式
const MODE_DRIVERS = new Set(['ark_video', 'local_h3_gateway', 'minimax_video', 'volcano_video'])

interface Props {
  provider: ModelProvider
  editing?: ProviderModel
  initial?: { driver: string; model: string }
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
  const drivers = provider.preset.drivers
  const [driver, setDriver] = useState(editing?.driver || initial?.driver || drivers[0])
  const info = DRIVERS[driver]
  const isChat = info?.modality === 'chat'
  const pricing0 = editing?.pricing || {}
  const [model, setModel] = useState(editing?.model ?? initial?.model ?? info?.defaultModel ?? '')
  const [name, setName] = useState(editing?.name || '')
  const [unit, setUnit] = useState<PriceUnit>(() =>
    (Object.keys(PRICE_UNITS) as PriceUnit[]).find((key) => !key.startsWith('input_') && String(pricing0[key] ?? '').trim()) || info?.priceUnit || 'output_per_second')
  const [amount, setAmount] = useState(() => String(pricing0[unit] ?? ''))
  const [chatPrice, setChatPrice] = useState<Record<string, string>>(() => pick(pricing0, CHAT_PRICE_KEYS))
  const [capabilities, setCapabilities] = useState(JSON.stringify(editing?.capabilities || defaultCapabilities(driver), null, 2))
  const [config, setConfig] = useState(JSON.stringify(editing?.config || {}, null, 2))
  const [extraPricing, setExtraPricing] = useState(JSON.stringify(
    Object.fromEntries(Object.entries(pricing0).filter(([key]) => !MANAGED_PRICE_KEYS.has(key) && key !== 'currency')), null, 2))
  const [busy, setBusy] = useState(false)

  const capabilityObject = (): Record<string, unknown> => {
    try { return parseObject('能力', capabilities) } catch { return {} }
  }
  const modes = (capabilityObject().modes as string[] | undefined) || ['text']
  const toggleCapability = (key: string, on: boolean) => {
    const current = capabilityObject()
    if (key in Object.fromEntries(VIDEO_MODES)) {
      const next = on ? [...new Set([...modes, key])] : modes.filter((mode) => mode !== key)
      current.modes = next.length ? next : ['text']
    } else {
      current[key] = on
    }
    setCapabilities(JSON.stringify(current, null, 2))
  }

  const changeDriver = (value: string) => {
    setDriver(value)
    if (!editing) {
      setModel(DRIVERS[value]?.defaultModel || '')
      setUnit(DRIVERS[value]?.priceUnit && !DRIVERS[value].priceUnit.startsWith('input_') ? DRIVERS[value].priceUnit : 'output_per_second')
      setCapabilities(JSON.stringify(defaultCapabilities(value), null, 2))
    }
  }

  const buildPricing = (): Record<string, unknown> => {
    const pricing: Record<string, unknown> = { currency: 'CNY', ...parseObject('其他价格字段', extraPricing) }
    if (isChat) {
      for (const key of CHAT_PRICE_KEYS) if (chatPrice[key].trim()) pricing[key] = chatPrice[key].trim()
    } else if (amount.trim()) {
      pricing[unit] = amount.trim()
    }
    return pricing
  }

  const save = async () => {
    setBusy(true)
    try {
      if (!model.trim()) throw new Error('填写模型 ID')
      const body = {
        name: name.trim() || model.trim(),
        model: model.trim(),
        config: parseObject('配置', config),
        capabilities: parseObject('能力', capabilities),
        pricing: buildPricing(),
      }
      if (editing) {
        // 编辑只改模型自身；地址与密钥由供应商统一管理，不在这里发送。
        await modelServicesApi.update(editing.slug, body)
      } else {
        await providersApi.addModel(provider.slug, { driver, ...body })
      }
      await onSaved(editing ? `${body.name} 已更新` : `${body.name} 已添加`)
    } catch (error) {
      onError(error instanceof Error ? error.message : '保存失败')
    } finally {
      setBusy(false)
    }
  }

  return <div className="ms-dialog" role="dialog" aria-label={editing ? '编辑模型' : '添加模型'}>
    <div className="ms-dialog__panel">
      <div className="ms-dialog__head">
        <strong>{editing ? `编辑 ${editing.name}` : `添加模型 · ${provider.name}`}</strong>
        <button className="ms-icon-btn" onClick={onClose} aria-label="关闭"><X size={16} /></button>
      </div>
      <div className="ms-form">
        <label>类型
          <select className="settings-input" disabled={!!editing} value={driver} onChange={(e) => changeDriver(e.target.value)}>
            {drivers.map((key) => <option key={key} value={key}>{DRIVERS[key]?.label || key}</option>)}
          </select>
        </label>
        <label>模型 ID<input className="settings-input" value={model} placeholder={info?.defaultModel || (isChat ? 'deepseek-chat' : '厂商控制台中的模型 ID')} onChange={(e) => setModel(e.target.value)} /></label>
        <label className="ms-form__wide">显示名称<input className="settings-input" value={name} placeholder="留空则使用模型 ID" onChange={(e) => setName(e.target.value)} /></label>

        {MODE_DRIVERS.has(driver) && <fieldset className="ms-form__wide ms-checks"><legend>支持的生成方式</legend>
          {VIDEO_MODES.map(([key, label]) => <label key={key} className="ms-check">
            <input type="checkbox" checked={modes.includes(key)} onChange={(e) => toggleCapability(key, e.target.checked)} />{label}
          </label>)}
        </fieldset>}
        {isChat && <fieldset className="ms-form__wide ms-checks"><legend>能力</legend>
          {CHAT_FLAGS.map(([key, label]) => <label key={key} className="ms-check">
            <input type="checkbox" checked={capabilityObject()[key] === true} onChange={(e) => toggleCapability(key, e.target.checked)} />{label}
          </label>)}
        </fieldset>}

        {isChat ? <>
          <label>输入价（元 / 百万 Token）<input className="settings-input" inputMode="decimal" value={chatPrice.input_per_million_tokens} onChange={(e) => setChatPrice({ ...chatPrice, input_per_million_tokens: e.target.value })} /></label>
          <label>输出价（元 / 百万 Token）<input className="settings-input" inputMode="decimal" value={chatPrice.output_per_million_tokens} onChange={(e) => setChatPrice({ ...chatPrice, output_per_million_tokens: e.target.value })} /></label>
          <label>缓存命中价（可选）<input className="settings-input" inputMode="decimal" value={chatPrice.cached_input_per_million_tokens} onChange={(e) => setChatPrice({ ...chatPrice, cached_input_per_million_tokens: e.target.value })} /></label>
        </> : <>
          <label>计价单位
            <select className="settings-input" value={unit} onChange={(e) => setUnit(e.target.value as PriceUnit)}>
              {(Object.keys(PRICE_UNITS) as PriceUnit[]).filter((key) => !key.startsWith('input_')).map((key) => <option key={key} value={key}>{PRICE_UNITS[key]}</option>)}
            </select>
          </label>
          <label>单价<input className="settings-input" inputMode="decimal" value={amount} placeholder="不填写的视频模型不会被选中" onChange={(e) => setAmount(e.target.value)} /></label>
        </>}
      </div>
      <details className="ms-advanced"><summary><ChevronDown size={14} />高级（JSON）</summary>
        <label>能力<textarea className="settings-textarea" value={capabilities} onChange={(e) => setCapabilities(e.target.value)} /></label>
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
