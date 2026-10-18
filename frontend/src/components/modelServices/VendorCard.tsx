import { useState } from 'react'
import { KeyRound, Plus, Trash2 } from 'lucide-react'
import { ModelDeployment, modelServicesApi } from '../../modelServicesApi'
import { DRIVERS, Vendor, hostOf, priceLabel } from './vendors'

interface Props {
  vendor: Vendor
  items: ModelDeployment[]
  availability: Record<string, boolean>
  onAddModel: () => void
  onEdit: (item: ModelDeployment) => void
  onChanged: () => Promise<void>
  notify: (ok: boolean, text: string) => void
}

export function statusOf(item: ModelDeployment, available: boolean | undefined): [string, string] {
  if (!item.enabled) return ['已停用', 'off']
  if (!item.credential_configured) return ['缺少 Key', 'unset']
  if (available === false) return ['不可连接', 'error']
  return ['正常', 'on']
}

export default function VendorCard({ vendor, items, availability, onAddModel, onEdit, onChanged, notify }: Props) {
  const [keyInput, setKeyInput] = useState('')
  const [editingKey, setEditingKey] = useState(false)
  const keyConfigured = items.some((item) => item.credential_configured)
  const hosts = [...new Set(items.map((item) => hostOf(item.base_url)).filter(Boolean))]

  const saveKey = async () => {
    // 同一厂商的模型共用一个密钥引用，写入任意一个即对全部生效。
    if (!keyInput.trim() || !items[0]) return
    try {
      await modelServicesApi.credential(items[0].slug, keyInput.trim())
      setKeyInput(''); setEditingKey(false)
      notify(true, `${vendor.name} 的 API Key 已保存`)
      await onChanged()
    } catch (error) { notify(false, error instanceof Error ? error.message : 'API Key 保存失败') }
  }

  const act = async (action: () => Promise<unknown>, success?: string) => {
    try {
      await action()
      if (success) notify(true, success)
      await onChanged()
    } catch (error) { notify(false, error instanceof Error ? error.message : '操作失败') }
  }

  return <section className="vendor-card" aria-label={vendor.name}>
    <header className="vendor-card__head">
      <div>
        <div className="vendor-card__name">{vendor.name}{vendor.selfHosted && <span className="vendor-badge">自建</span>}{vendor.custom && <span className="vendor-badge">自定义</span>}</div>
        <div className="vendor-card__desc">{hosts.join(' · ') || vendor.description}</div>
      </div>
      <div className="vendor-card__actions">
        <span className={`runtime-model-card__state runtime-model-card__state--${keyConfigured ? 'on' : 'unset'}`}>{keyConfigured ? 'Key 已配置' : '未配置 Key'}</span>
        {items.length > 0 && <button className="model-action" onClick={() => setEditingKey(!editingKey)}><KeyRound size={13} />{keyConfigured ? '更换 Key' : '设置 Key'}</button>}
        <button className="model-action" onClick={onAddModel}><Plus size={13} />添加模型</button>
      </div>
    </header>
    {editingKey && <div className="vendor-card__key">
      <input type="password" autoComplete="new-password" className="settings-input" placeholder={`${vendor.name} API Key（只写入，不回显）`} value={keyInput} onChange={(e) => setKeyInput(e.target.value)} />
      <button className="settings-save-btn" disabled={!keyInput.trim()} onClick={() => void saveKey()}>保存</button>
    </div>}
    {items.length === 0 ? <div className="vendor-card__empty">还没有模型，点击「添加模型」接入。</div> : <div className="vendor-models">
      {items.map((item) => {
        const [label, tone] = statusOf(item, availability[item.slug])
        return <div className="vendor-model" key={item.slug}>
          <div className="vendor-model__main">
            <div className="vendor-model__title">{item.name}<span className={`runtime-model-card__state runtime-model-card__state--${tone}`}>{label}</span></div>
            <div className="vendor-model__meta"><span>{DRIVERS[item.driver]?.label || item.driver}</span><code>{item.model}</code><span className={priceLabel(item.pricing) === '未定价' ? 'vendor-model__price--missing' : ''}>{priceLabel(item.pricing)}</span></div>
          </div>
          <div className="model-card-actions">
            <button className="model-action" onClick={() => onEdit(item)}>编辑</button>
            <button className="model-action" onClick={() => void act(() => modelServicesApi.health(item.slug), `${item.name} 连接正常`)}>测试</button>
            <button className="model-action" onClick={() => void act(() => modelServicesApi.enable(item.slug, !item.enabled))}>{item.enabled ? '停用' : '启用'}</button>
            <button className="model-action model-action--danger" aria-label={`删除 ${item.name}`} onClick={() => { if (confirm(`确定删除 ${item.name}？`)) void act(() => modelServicesApi.remove(item.slug)) }}><Trash2 size={13} /></button>
          </div>
        </div>
      })}
    </div>}
  </section>
}
