import { useState } from 'react'
import { ChevronDown, Save, X } from 'lucide-react'
import { ModelDeployment, modelServicesApi } from '../../modelServicesApi'
import { DRIVERS, PRICE_UNITS, PriceUnit, Vendor, slugFor } from './vendors'

const CHAT_PRICE_KEYS = ['input_per_million_tokens', 'cached_input_per_million_tokens', 'output_per_million_tokens'] as const
const MANAGED_PRICE_KEYS = new Set<string>([...Object.keys(PRICE_UNITS), ...CHAT_PRICE_KEYS])

interface Props {
  vendor: Vendor
  editing?: ModelDeployment
  onSaved: (message: string) => void
  onCancel: () => void
  onError: (message: string) => void
}

function initialPrice(pricing: Record<string, unknown>, fallback: PriceUnit): { unit: PriceUnit; amount: string } {
  const unit = (Object.keys(PRICE_UNITS) as PriceUnit[]).find((key) => pricing[key] !== undefined && String(pricing[key]).trim() !== '')
  return unit ? { unit, amount: String(pricing[unit]) } : { unit: fallback, amount: '' }
}

function withoutManagedKeys(pricing: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(pricing).filter(([key]) => !MANAGED_PRICE_KEYS.has(key)))
}

function parseJson(label: string, text: string): Record<string, unknown> {
  try {
    const value = JSON.parse(text || '{}')
    if (value && typeof value === 'object' && !Array.isArray(value)) return value
  } catch { /* 统一在下面报错 */ }
  throw new Error(`${label} 不是合法的 JSON 对象`)
}

export default function ModelForm({ vendor, editing, onSaved, onCancel, onError }: Props) {
  const [driver, setDriver] = useState(editing?.driver || vendor.drivers[0])
  const info = DRIVERS[driver]
  const isChat = info?.modality === 'chat'
  const existingPricing = editing?.pricing || {}
  const [name, setName] = useState(editing?.name || '')
  const [model, setModel] = useState(editing?.model ?? info?.defaultModel ?? '')
  const [baseUrl, setBaseUrl] = useState(editing?.base_url ?? vendor.baseUrl)
  const [apiKey, setApiKey] = useState('')
  const [price, setPrice] = useState(() => initialPrice(existingPricing, info?.priceUnit || 'output_per_second'))
  const [chatPrice, setChatPrice] = useState<Record<string, string>>(() =>
    Object.fromEntries(CHAT_PRICE_KEYS.map((key) => [key, existingPricing[key] !== undefined ? String(existingPricing[key]) : ''])),
  )
  const [config, setConfig] = useState(JSON.stringify(editing?.config || {}, null, 2))
  const [capabilities, setCapabilities] = useState(JSON.stringify(editing?.capabilities || {}, null, 2))
  const [extraPricing, setExtraPricing] = useState(JSON.stringify(withoutManagedKeys(existingPricing), null, 2))
  const [busy, setBusy] = useState(false)

  const changeDriver = (value: string) => {
    setDriver(value)
    if (!editing) {
      setModel(DRIVERS[value]?.defaultModel || '')
      setPrice({ unit: DRIVERS[value]?.priceUnit || 'output_per_second', amount: '' })
    }
  }

  const buildPricing = (): Record<string, unknown> => {
    const pricing: Record<string, unknown> = { currency: 'CNY', ...parseJson('其他价格字段', extraPricing) }
    if (isChat) {
      for (const key of CHAT_PRICE_KEYS) if (chatPrice[key].trim()) pricing[key] = chatPrice[key].trim()
    } else if (price.amount.trim()) {
      pricing[price.unit] = price.amount.trim()
    }
    return pricing
  }

  const save = async () => {
    setBusy(true)
    try {
      if (!model.trim()) throw new Error('请填写模型 ID')
      const common = {
        name: name.trim() || `${vendor.name} · ${model.trim()}`,
        model: model.trim(),
        base_url: baseUrl.trim(),
        config: parseJson('配置', config),
        capabilities: parseJson('能力', capabilities),
        pricing: buildPricing(),
      }
      // 编辑时不发送 driver / credential_ref：密钥引用由厂商决定，不能被表单默认值覆盖。
      const saved = editing
        ? await modelServicesApi.update(editing.slug, common)
        : await modelServicesApi.create({
          ...common,
          slug: slugFor(vendor, model.trim()),
          driver,
          modality: info.modality,
          credential_ref: vendor.credentialRef,
          enabled: true,
          priority: 100,
        })
      if (apiKey.trim()) await modelServicesApi.credential(saved.slug, apiKey.trim())
      onSaved(editing ? `${saved.name} 已更新` : `${saved.name} 已添加并热加载`)
    } catch (error) {
      onError(error instanceof Error ? error.message : '保存失败')
    } finally {
      setBusy(false)
    }
  }

  return <div className="model-form" role="form" aria-label={editing ? '编辑模型' : '添加模型'}>
    <div className="model-form__head">
      <strong>{editing ? `编辑 ${editing.name}` : `为「${vendor.name}」添加模型`}</strong>
      <button className="model-action" onClick={onCancel} aria-label="关闭"><X size={14} /></button>
    </div>
    <div className="model-form__grid">
      <label>接入类型
        <select className="settings-input" disabled={!!editing} value={driver} onChange={(e) => changeDriver(e.target.value)}>
          {vendor.drivers.map((key) => <option key={key} value={key}>{DRIVERS[key]?.label || key}</option>)}
        </select>
      </label>
      <label>模型 ID<input className="settings-input" placeholder="厂商控制台中的模型 ID" value={model} onChange={(e) => setModel(e.target.value)} /></label>
      <label>显示名称<input className="settings-input" placeholder="留空自动生成" value={name} onChange={(e) => setName(e.target.value)} /></label>
      <label>服务地址<input className="settings-input" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder={vendor.selfHosted ? 'http://192.168.x.x:8088' : ''} /></label>
      <label className="model-form__wide">API Key
        <input type="password" autoComplete="new-password" className="settings-input" value={apiKey}
          placeholder={vendor.selfHosted ? 'Gateway Bearer Token（留空保持不变）' : '留空则沿用该厂商已保存的 Key'}
          onChange={(e) => setApiKey(e.target.value)} />
      </label>
      {isChat ? CHAT_PRICE_KEYS.map((key) => <label key={key}>
        {key === 'input_per_million_tokens' ? '输入价（元 / 百万 Token）' : key === 'output_per_million_tokens' ? '输出价（元 / 百万 Token）' : '缓存命中价（可选）'}
        <input className="settings-input" inputMode="decimal" value={chatPrice[key]} onChange={(e) => setChatPrice({ ...chatPrice, [key]: e.target.value })} />
      </label>) : <>
        <label>计价单位
          <select className="settings-input" value={price.unit} onChange={(e) => setPrice({ ...price, unit: e.target.value as PriceUnit })}>
            {(Object.keys(PRICE_UNITS) as PriceUnit[]).filter((unit) => !unit.startsWith('input_')).map((unit) => <option key={unit} value={unit}>{PRICE_UNITS[unit]}</option>)}
          </select>
        </label>
        <label>单价<input className="settings-input" inputMode="decimal" placeholder="未填写的视频模型不会被路由选中" value={price.amount} onChange={(e) => setPrice({ ...price, amount: e.target.value })} /></label>
      </>}
    </div>
    <details><summary><ChevronDown size={14} />高级设置（JSON）</summary>
      <label>配置<textarea className="settings-textarea" value={config} onChange={(e) => setConfig(e.target.value)} /></label>
      <label>能力（如图生视频模式 modes、时长范围）<textarea className="settings-textarea" value={capabilities} onChange={(e) => setCapabilities(e.target.value)} /></label>
      <label>其他价格字段（如按条计费 per_piece_6）<textarea className="settings-textarea" value={extraPricing} onChange={(e) => setExtraPricing(e.target.value)} /></label>
    </details>
    <button className="settings-save-btn" disabled={busy} onClick={() => void save()}><Save size={14} />{busy ? '保存中…' : '保存'}</button>
  </div>
}
