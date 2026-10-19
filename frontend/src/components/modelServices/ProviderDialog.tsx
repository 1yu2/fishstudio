import { useEffect, useRef, useState } from 'react'
import { Check, Eye, EyeOff, X } from 'lucide-react'
import { Capability, ModelProvider, ProviderInput, ProviderType, providersApi } from '../../modelServicesApi'

interface Props {
  types: ProviderType[]
  editing?: ModelProvider
  onClose: () => void
  onSaved: (provider: ModelProvider, message: string) => Promise<void> | void
  onError: (message: string) => void
}

const ID_PATTERN = /^[a-z0-9][a-z0-9_-]{1,62}[a-z0-9]$/

function envFromId(id: string) {
  const core = id.toUpperCase().replace(/[^A-Z0-9]+/g, '_').replace(/^_+|_+$/g, '')
  return core ? `${core}_API_KEY` : ''
}

/** 能力多选：已选以标签显示，下拉列出该 Provider Type 支持的能力 */
function CapabilitySelect({ options, value, onChange }: { options: Capability[]; value: Capability[]; onChange: (next: Capability[]) => void }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const close = (event: MouseEvent) => { if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])
  const toggle = (item: Capability) => onChange(value.includes(item) ? value.filter((cap) => cap !== item) : options.filter((cap) => cap === item || value.includes(cap)))
  return <div className="pv-multi" ref={ref}>
    <div className={`pv-multi__box${open ? ' pv-multi__box--open' : ''}`} role="combobox" aria-expanded={open} aria-label="能力" tabIndex={0}
      onClick={() => setOpen(!open)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpen(!open) } }}>
      {value.map((item) => <span className="pv-chip" key={item}>{item}
        <button type="button" aria-label={`移除 ${item}`} onClick={(e) => { e.stopPropagation(); toggle(item) }}><X size={11} /></button>
      </span>)}
    </div>
    {open && <div className="pv-multi__list" role="listbox" aria-multiselectable="true">
      {options.map((item) => <div key={item} role="option" aria-selected={value.includes(item)} className={`pv-multi__option${value.includes(item) ? ' pv-multi__option--on' : ''}`} onClick={() => toggle(item)}>
        {item}{value.includes(item) && <Check size={14} />}
      </div>)}
    </div>}
  </div>
}

export default function ProviderDialog({ types, editing, onClose, onSaved, onError }: Props) {
  const initialType = editing?.provider_type.id || types[0]?.id || 'openai'
  const [slug, setSlug] = useState(editing?.slug || '')
  const [name, setName] = useState(editing?.name || '')
  const [typeId, setTypeId] = useState(initialType)
  const type = types.find((item) => item.id === typeId)
  const [baseUrl, setBaseUrl] = useState(editing?.base_url || '')
  const [envName, setEnvName] = useState(editing?.api_key_env || '')
  const [envTouched, setEnvTouched] = useState(!!editing)
  const [apiKey, setApiKey] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [endpoint, setEndpoint] = useState(editing?.models_endpoint ?? type?.default_models_endpoint ?? '/models')
  const [capabilities, setCapabilities] = useState<Capability[]>(editing?.capabilities || (type?.capabilities.includes('chat') ? ['chat'] : type?.capabilities.slice(0, 1) || []))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const changeType = (id: string) => {
    const next = types.find((item) => item.id === id)
    setTypeId(id)
    if (!next) return
    setEndpoint(next.default_models_endpoint)
    setCapabilities(next.capabilities.includes('chat') ? ['chat'] : next.capabilities.slice(0, 1))
  }

  const changeSlug = (value: string) => {
    const normalized = value.toLowerCase()
    setSlug(normalized)
    if (!envTouched) setEnvName(envFromId(normalized))
  }

  const save = async () => {
    setError('')
    if (!editing && !ID_PATTERN.test(slug)) return setError('Provider ID 只能是小写字母、数字、- 和 _，3～64 个字符')
    if (!baseUrl.trim() && !type?.default_base_url) return setError('请填写 Base URL')
    if (!capabilities.length) return setError('至少选择一种能力')
    setBusy(true)
    try {
      const body: ProviderInput = {
        name: name.trim() || slug,
        base_url: baseUrl.trim() || type?.default_base_url,
        api_key_env: envName.trim() || envFromId(slug),
        models_endpoint: endpoint.trim(),
        capabilities,
      }
      let saved: ModelProvider
      if (editing) {
        saved = await providersApi.update(editing.slug, body)
        if (apiKey.trim()) await providersApi.credential(editing.slug, apiKey.trim())
      } else {
        saved = await providersApi.create({ ...body, slug, provider_type: typeId, api_key: apiKey.trim() || undefined })
      }
      await onSaved(saved, editing ? `${saved.name} 已更新` : `已新增 ${saved.name}`)
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : '保存失败'
      setError(message); onError(message)
    } finally { setBusy(false) }
  }

  return <div className="ms-dialog" role="dialog" aria-label={editing ? '编辑供应商' : '新增供应商'}>
    <div className="ms-dialog__panel pv-dialog">
      <div className="ms-dialog__head"><strong>{editing ? '编辑供应商' : '新增供应商'}</strong><button className="ms-icon-btn" aria-label="关闭" onClick={onClose}><X size={16} /></button></div>
      <div className="pv-form">
        <label>Provider ID<input className="settings-input" value={slug} placeholder="my-provider" disabled={!!editing} onChange={(e) => changeSlug(e.target.value)} /></label>
        <label>展示名称<input className="settings-input" value={name} placeholder="My Provider" onChange={(e) => setName(e.target.value)} /></label>
        <label>Base URL<input className="settings-input" value={baseUrl} placeholder={type?.default_base_url || 'https://api.example.com/v1'} onChange={(e) => setBaseUrl(e.target.value)} /></label>
        <label>Provider Type
          <select className="settings-input" value={typeId} disabled={!!editing} onChange={(e) => changeType(e.target.value)}>
            {types.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
          </select>
        </label>
        <label>API Key Env<input className="settings-input" value={envName} placeholder="环境变量名" onChange={(e) => { setEnvTouched(true); setEnvName(e.target.value.toUpperCase()) }} /></label>
        <label>API Key
          <span className="ms-input-wrap">
            <input className="settings-input" type={showKey ? 'text' : 'password'} autoComplete="new-password" value={apiKey}
              placeholder={editing ? (editing.credential_configured ? '已配置，留空则不修改' : '未配置，填写后保存') : ''} onChange={(e) => setApiKey(e.target.value)} />
            <button type="button" className="ms-icon-btn" aria-label={showKey ? '隐藏 API Key' : '显示 API Key'} onClick={() => setShowKey(!showKey)}>{showKey ? <Eye size={15} /> : <EyeOff size={15} />}</button>
          </span>
        </label>
        <label>Models Endpoint<input className="settings-input" value={endpoint} placeholder="/models" onChange={(e) => setEndpoint(e.target.value)} /></label>
        <span />
        <div className="pv-form__wide">
          <div className="pv-label">能力</div>
          <CapabilitySelect options={type?.capabilities || []} value={capabilities} onChange={setCapabilities} />
        </div>
      </div>
      {error && <div className="ms-check-result ms-check-result--error">{error}</div>}
      <div className="ms-dialog__foot">
        <button className="model-action" onClick={onClose}>取消</button>
        <button className="settings-save-btn" disabled={busy} onClick={() => void save()}>{busy ? '保存中…' : '保存'}</button>
      </div>
    </div>
  </div>
}
