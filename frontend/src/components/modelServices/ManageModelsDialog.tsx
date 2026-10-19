import { useState } from 'react'
import { Plus, RefreshCw, Settings2, Trash2, X } from 'lucide-react'
import { ModelProvider, ProviderModel, RemoteModel, modelServicesApi, providersApi } from '../../modelServicesApi'
import ModelDialog from './ModelDialog'
import { CAPABILITY_ORDER, capabilityTags, priceLabel } from './labels'

interface Props {
  provider: ModelProvider
  onClose: () => void
  onChanged: () => Promise<void>
  notify: (ok: boolean, text: string) => void
}

type Sub = { kind: 'model'; editing?: ProviderModel; initial?: { capability: ProviderModel['capability']; model: string } } | null

export function Switch({ checked, onChange, label }: { checked: boolean; onChange: (value: boolean) => void; label: string }) {
  return <button type="button" role="switch" aria-checked={checked} aria-label={label} className={`ms-switch${checked ? ' ms-switch--on' : ''}`} onClick={(e) => { e.stopPropagation(); onChange(!checked) }}><span /></button>
}

export default function ManageModelsDialog({ provider, onClose, onChanged, notify }: Props) {
  const [sub, setSub] = useState<Sub>(null)
  const [remote, setRemote] = useState<RemoteModel[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [checkResult, setCheckResult] = useState<{ ok: boolean; message: string } | null>(null)

  const run = async (action: () => Promise<unknown>, success?: string) => {
    try { await action(); if (success) notify(true, success); await onChanged() }
    catch (error) { notify(false, error instanceof Error ? error.message : '操作失败') }
  }

  const fetchRemote = async () => {
    setLoading(true)
    try { setRemote(await providersApi.remoteModels(provider.slug)) }
    catch (error) { notify(false, error instanceof Error ? error.message : '获取模型列表失败') }
    finally { setLoading(false) }
  }

  const groups = CAPABILITY_ORDER
    .map((capability) => [capability, provider.models.filter((model) => model.capability === capability)] as const)
    .filter(([, models]) => models.length > 0)
  const added = new Set(provider.models.map((model) => model.model))

  return <div className="ms-dialog" role="dialog" aria-label={`管理模型 ${provider.name}`}>
    <div className="ms-dialog__panel ms-dialog__panel--wide">
      <div className="ms-dialog__head">
        <div><strong>{provider.name}</strong><code className="pv-slug">{provider.slug}</code></div>
        <button className="ms-icon-btn" aria-label="关闭" onClick={onClose}><X size={16} /></button>
      </div>
      <div className="pv-manage__bar">
        <button className="model-action" onClick={() => void providersApi.check(provider.slug).then(setCheckResult).catch((error) => setCheckResult({ ok: false, message: String(error.message || error) }))}>检查连接</button>
        {provider.models_endpoint && <button className="model-action" disabled={loading} onClick={() => void fetchRemote()}><RefreshCw size={13} />{loading ? '获取中…' : '获取模型列表'}</button>}
        <button className="model-action" onClick={() => setSub({ kind: 'model' })}><Plus size={13} />添加模型</button>
        {checkResult && <span className={`ms-check-result ms-check-result--${checkResult.ok ? 'ok' : 'error'}`}>{checkResult.message}</span>}
      </div>

      {remote && <div className="pv-remote">
        <div className="pv-remote__head"><span>{provider.models_endpoint} 返回 {remote.length} 个可用模型</span><button className="ms-icon-btn" aria-label="收起模型列表" onClick={() => setRemote(null)}><X size={14} /></button></div>
        {remote.map((item) => <div className="ms-model" key={item.model}>
          <div className="ms-model__main"><div className="ms-model__name">{item.model}<span className="ms-tag ms-tag--muted">{item.capability}</span></div></div>
          {item.added || added.has(item.model) ? <span className="ms-tag">已添加</span> :
            <button className="model-action" onClick={() => setSub({ kind: 'model', initial: { capability: item.capability, model: item.model } })}><Plus size={13} />添加</button>}
        </div>)}
      </div>}

      {groups.length === 0 && <div className="ms-empty">还没有模型。{provider.models_endpoint ? '可以「获取模型列表」，或' : ''}点击「添加模型」手动填写 Model ID。</div>}
      {groups.map(([capability, models]) => <div className="ms-model-group" key={capability}>
        <div className="ms-model-group__title">{capability}</div>
        {models.map((model) => <div className="ms-model" key={model.slug}>
          <div className="ms-model__main">
            <div className="ms-model__name">
              {model.name}
              {model.name !== model.model && <code>{model.model}</code>}
              {capabilityTags(model.capabilities).map((tag) => <span className="ms-tag" key={tag}>{tag}</span>)}
            </div>
            <div className="ms-model__meta">
              <span className={priceLabel(model.pricing, model.modality) === '未定价' ? 'ms-warn' : ''}>{priceLabel(model.pricing, model.modality)}</span>
              {model.enabled && provider.enabled && model.available === false && <span className="ms-warn">不可用（检查 API Key 或 Base URL）</span>}
            </div>
          </div>
          <button className="ms-icon-btn" aria-label={`设置 ${model.name}`} onClick={() => setSub({ kind: 'model', editing: model })}><Settings2 size={15} /></button>
          <button className="ms-icon-btn ms-icon-btn--danger" aria-label={`删除 ${model.name}`}
            onClick={() => { if (confirm(`删除模型 ${model.name}？`)) void run(() => modelServicesApi.remove(model.slug), '模型已删除') }}><Trash2 size={14} /></button>
          <Switch label={`启用 ${model.name}`} checked={model.enabled} onChange={(value) => void run(() => modelServicesApi.enable(model.slug, value))} />
        </div>)}
      </div>)}
    </div>

    {sub && <ModelDialog provider={provider} editing={sub.editing} initial={sub.initial}
      onClose={() => setSub(null)} onError={(text) => notify(false, text)}
      onSaved={async (text) => { notify(true, text); setSub(null); await onChanged() }} />}
  </div>
}
