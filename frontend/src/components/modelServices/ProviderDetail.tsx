import { useEffect, useState } from 'react'
import { Eye, EyeOff, Plus, RefreshCw, Settings2, Trash2, X } from 'lucide-react'
import { ModelProvider, ProviderModel, RemoteModel, modelServicesApi, providersApi } from '../../modelServicesApi'
import ModelDialog from './ModelDialog'
import { DRIVERS, MODALITY_LABELS, MODALITY_ORDER, capabilityTags, priceLabel } from './labels'

interface Props {
  provider: ModelProvider
  onChanged: () => Promise<void>
  onDeleted: () => Promise<void>
  notify: (ok: boolean, text: string) => void
}

type Dialog = { kind: 'model'; editing?: ProviderModel; initial?: { driver: string; model: string } } | { kind: 'remote'; items: RemoteModel[] } | null

export function Switch({ checked, onChange, label }: { checked: boolean; onChange: (value: boolean) => void; label: string }) {
  return <button type="button" role="switch" aria-checked={checked} aria-label={label} className={`ms-switch${checked ? ' ms-switch--on' : ''}`} onClick={() => onChange(!checked)}><span /></button>
}

export default function ProviderDetail({ provider, onChanged, onDeleted, notify }: Props) {
  const [apiKey, setApiKey] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [baseUrl, setBaseUrl] = useState(provider.base_url)
  const [checking, setChecking] = useState(false)
  const [checkResult, setCheckResult] = useState<{ ok: boolean; message: string } | null>(null)
  const [dialog, setDialog] = useState<Dialog>(null)
  const [loadingRemote, setLoadingRemote] = useState(false)

  useEffect(() => { setBaseUrl(provider.base_url); setApiKey(''); setCheckResult(null) }, [provider.slug, provider.base_url])

  const run = async (action: () => Promise<unknown>, success?: string) => {
    try {
      await action()
      if (success) notify(true, success)
      await onChanged()
    } catch (error) { notify(false, error instanceof Error ? error.message : '操作失败') }
  }

  const check = async () => {
    setChecking(true); setCheckResult(null)
    try {
      if (apiKey.trim()) { await providersApi.credential(provider.slug, apiKey.trim()); setApiKey(''); await onChanged() }
      setCheckResult(await providersApi.check(provider.slug))
    } catch (error) { setCheckResult({ ok: false, message: error instanceof Error ? error.message : '检查失败' }) }
    finally { setChecking(false) }
  }

  const fetchRemote = async () => {
    setLoadingRemote(true)
    try { setDialog({ kind: 'remote', items: await providersApi.remoteModels(provider.slug) }) }
    catch (error) { notify(false, error instanceof Error ? error.message : '拉取模型列表失败') }
    finally { setLoadingRemote(false) }
  }

  const groups = MODALITY_ORDER
    .map((modality) => [modality, provider.models.filter((model) => model.modality === modality)] as const)
    .filter(([, models]) => models.length > 0)
  const urlChanged = baseUrl.trim().replace(/\/+$/, '') !== provider.base_url
  const needsUrl = provider.preset.custom || provider.preset.self_hosted || !!provider.preset.default_base_url

  return <section className="ms-detail" aria-label={`${provider.name} 设置`}>
    <header className="ms-detail__head">
      <div>
        <h3>{provider.name}</h3>
        <p>{provider.preset.description}</p>
      </div>
      <Switch label={`启用 ${provider.name}`} checked={provider.enabled}
        onChange={(value) => void run(() => providersApi.update(provider.slug, { enabled: value }), value ? '已启用' : '已停用，其下模型不会被使用')} />
    </header>

    <div className="ms-field">
      <label htmlFor="ms-api-key">API 密钥</label>
      <div className="ms-field__row">
        <div className="ms-input-wrap">
          <input id="ms-api-key" className="settings-input" type={showKey ? 'text' : 'password'} autoComplete="new-password" value={apiKey}
            placeholder={provider.credential_configured ? '已保存（输入新密钥可替换）' : provider.preset.self_hosted ? 'Gateway Bearer Token' : '粘贴 API 密钥'}
            onChange={(e) => setApiKey(e.target.value)} />
          <button className="ms-icon-btn" aria-label={showKey ? '隐藏密钥' : '显示密钥'} onClick={() => setShowKey(!showKey)}>{showKey ? <EyeOff size={15} /> : <Eye size={15} />}</button>
        </div>
        {apiKey.trim() && <button className="model-action" onClick={() => void run(async () => { await providersApi.credential(provider.slug, apiKey.trim()); setApiKey('') }, '密钥已保存')}>保存</button>}
        <button className="model-action" disabled={checking} onClick={() => void check()}>{checking ? '检查中…' : '检查'}</button>
      </div>
      {checkResult && <div className={`ms-check-result ms-check-result--${checkResult.ok ? 'ok' : 'error'}`}>{checkResult.message}</div>}
      <small>密钥只写入服务器，不会再显示。该供应商下的所有模型共用这把密钥。</small>
    </div>

    {needsUrl && <div className="ms-field">
      <label htmlFor="ms-base-url">API 地址</label>
      <div className="ms-field__row">
        <input id="ms-base-url" className="settings-input" value={baseUrl} placeholder={provider.preset.self_hosted ? 'http://192.168.x.x:8088' : 'https://api.example.com/v1'} onChange={(e) => setBaseUrl(e.target.value)} />
        {urlChanged && <button className="model-action" onClick={() => void run(() => providersApi.update(provider.slug, { base_url: baseUrl.trim() }), 'API 地址已更新')}>保存</button>}
        {provider.preset.default_base_url && provider.preset.default_base_url !== provider.base_url &&
          <button className="model-action" onClick={() => setBaseUrl(provider.preset.default_base_url)}>重置</button>}
      </div>
    </div>}

    <div className="ms-models__head">
      <h4>模型</h4>
      {provider.preset.lists_models && <button className="model-action" disabled={loadingRemote} onClick={() => void fetchRemote()}><RefreshCw size={13} />{loadingRemote ? '获取中…' : '获取模型列表'}</button>}
      <button className="model-action" onClick={() => setDialog({ kind: 'model' })}><Plus size={13} />添加</button>
    </div>
    {groups.length === 0 && <div className="ms-empty">还没有模型。{provider.preset.lists_models ? '可以先「获取模型列表」，或' : ''}点击「添加」手动填写模型 ID。</div>}
    {groups.map(([modality, models]) => <div className="ms-model-group" key={modality}>
      <div className="ms-model-group__title">{MODALITY_LABELS[modality] || modality}</div>
      {models.map((model) => <div className="ms-model" key={model.slug}>
        <div className="ms-model__main">
          <div className="ms-model__name">
            {model.name}
            {model.name !== model.model && <code>{model.model}</code>}
            {capabilityTags(model.capabilities).map((tag) => <span className="ms-tag" key={tag}>{tag}</span>)}
          </div>
          <div className="ms-model__meta">
            <span>{DRIVERS[model.driver]?.label || model.driver}</span>
            <span className={priceLabel(model.pricing, model.modality) === '未定价' ? 'ms-warn' : ''}>{priceLabel(model.pricing, model.modality)}</span>
            {model.enabled && provider.enabled && model.available === false && <span className="ms-warn">不可用（检查密钥或地址）</span>}
          </div>
        </div>
        <button className="ms-icon-btn" aria-label={`设置 ${model.name}`} onClick={() => setDialog({ kind: 'model', editing: model })}><Settings2 size={15} /></button>
        <button className="ms-icon-btn ms-icon-btn--danger" aria-label={`删除 ${model.name}`}
          onClick={() => { if (confirm(`删除模型 ${model.name}？`)) void run(() => modelServicesApi.remove(model.slug), '模型已删除') }}><Trash2 size={14} /></button>
        <Switch label={`启用 ${model.name}`} checked={model.enabled} onChange={(value) => void run(() => modelServicesApi.enable(model.slug, value))} />
      </div>)}
    </div>)}

    <div className="ms-danger-zone">
      <button className="model-action model-action--danger" onClick={() => { if (confirm(`删除供应商 ${provider.name} 及其全部模型？`)) void (async () => { try { await providersApi.remove(provider.slug); notify(true, '供应商已删除'); await onDeleted() } catch (error) { notify(false, error instanceof Error ? error.message : '删除失败') } })() }}>
        <Trash2 size={13} />删除供应商
      </button>
    </div>

    {dialog?.kind === 'model' && <ModelDialog provider={provider} editing={dialog.editing} initial={dialog.initial}
      onClose={() => setDialog(null)} onError={(text) => notify(false, text)}
      onSaved={async (text) => { notify(true, text); setDialog(null); await onChanged() }} />}

    {dialog?.kind === 'remote' && <div className="ms-dialog" role="dialog" aria-label="可添加的模型">
      <div className="ms-dialog__panel">
        <div className="ms-dialog__head"><strong>{provider.name} 的模型</strong><button className="ms-icon-btn" aria-label="关闭" onClick={() => setDialog(null)}><X size={16} /></button></div>
        {dialog.items.length === 0 ? <div className="ms-empty">没有识别到可添加的模型。</div> : <div className="ms-remote-list">
          {dialog.items.map((item) => <div className="ms-model" key={`${item.driver}:${item.model}`}>
            <div className="ms-model__main"><div className="ms-model__name">{item.model}</div><div className="ms-model__meta"><span>{MODALITY_LABELS[item.modality] || item.modality}</span></div></div>
            {item.added ? <span className="ms-tag">已添加</span> :
              <button className="model-action" onClick={() => setDialog({ kind: 'model', initial: { driver: item.driver, model: item.model } })}><Plus size={13} />添加</button>}
          </div>)}
        </div>}
      </div>
    </div>}
  </section>
}
