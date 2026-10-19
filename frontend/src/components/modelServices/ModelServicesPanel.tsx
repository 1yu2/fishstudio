import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertCircle, Check, Plus, RefreshCw, Search } from 'lucide-react'
import { ModelProvider, ModelRoute, ProviderType, PurposeDefinition, modelServicesApi, providersApi } from '../../modelServicesApi'
import DefaultModels from './DefaultModels'
import ManageModelsDialog from './ManageModelsDialog'
import ProviderCard from './ProviderCard'
import ProviderDialog from './ProviderDialog'
import './modelServices.css'

type View = 'providers' | 'defaults'
type Dialog = { kind: 'create' } | { kind: 'edit'; slug: string } | { kind: 'manage'; slug: string } | null

export default function ModelServicesPanel() {
  const [view, setView] = useState<View>('providers')
  const [providers, setProviders] = useState<ModelProvider[]>([])
  const [types, setTypes] = useState<ProviderType[]>([])
  const [routes, setRoutes] = useState<ModelRoute[]>([])
  const [purposes, setPurposes] = useState<PurposeDefinition[]>([])
  const [query, setQuery] = useState('')
  const [dialog, setDialog] = useState<Dialog>(null)
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null)
  const [reloadError, setReloadError] = useState('')

  const notify = useCallback((ok: boolean, text: string) => setNotice({ ok, text }), [])

  const load = useCallback(async () => {
    try {
      const [providerRows, typeInfo, routeRows, purposeRows, runtime] = await Promise.all([
        providersApi.list(), providersApi.types(), modelServicesApi.routes(), modelServicesApi.purposes(), modelServicesApi.runtime(),
      ])
      setProviders(providerRows); setTypes(typeInfo.types); setRoutes(routeRows); setPurposes(purposeRows)
      setReloadError(runtime.last_reload_failure?.reason || '')
    } catch (error) { notify(false, error instanceof Error ? error.message : '加载失败') }
  }, [notify])

  useEffect(() => { void load() }, [load])

  const filtered = useMemo(() => {
    const keyword = query.trim().toLowerCase()
    if (!keyword) return providers
    return providers.filter((provider) => [provider.name, provider.slug, provider.base_url, ...provider.capabilities]
      .some((text) => text.toLowerCase().includes(keyword))
      || provider.models.some((model) => model.model.toLowerCase().includes(keyword)))
  }, [providers, query])

  const target = dialog && dialog.kind !== 'create' ? providers.find((provider) => provider.slug === dialog.slug) : undefined

  const run = async (action: () => Promise<unknown>, success?: string) => {
    try { await action(); if (success) notify(true, success); await load() }
    catch (error) { notify(false, error instanceof Error ? error.message : '操作失败') }
  }

  return <div className="model-services">
    <div className="settings-panel__title">模型服务</div>
    <div className="ms-tabs" role="tablist">
      <button role="tab" aria-selected={view === 'providers'} className={view === 'providers' ? 'ms-tab ms-tab--on' : 'ms-tab'} onClick={() => setView('providers')}>供应商</button>
      <button role="tab" aria-selected={view === 'defaults'} className={view === 'defaults' ? 'ms-tab ms-tab--on' : 'ms-tab'} onClick={() => setView('defaults')}>默认模型</button>
    </div>
    {notice && <div className={`settings-toast settings-toast--${notice.ok ? 'success' : 'error'}`}>{notice.ok ? <Check size={14} /> : <AlertCircle size={14} />}{notice.text}</div>}
    {reloadError && <div className="model-reload-error"><AlertCircle size={14} />最近一次加载模型配置失败：{reloadError}</div>}

    {view === 'providers' ? <>
      <div className="pv-toolbar">
        <div className="ms-search"><Search size={14} /><input placeholder="搜索供应商..." aria-label="搜索供应商" value={query} onChange={(e) => setQuery(e.target.value)} /></div>
        <button className="settings-save-btn pv-toolbar__add" onClick={() => setDialog({ kind: 'create' })}><Plus size={15} />新增供应商</button>
        <button className="ms-icon-btn pv-toolbar__refresh" aria-label="刷新" onClick={() => void load()}><RefreshCw size={15} /></button>
      </div>
      {filtered.length === 0 ? <div className="ms-empty ms-empty--center">{providers.length ? '没有匹配的供应商' : '还没有供应商，点击「新增供应商」开始'}</div>
        : <div className="pv-grid">
          {filtered.map((provider) => <ProviderCard key={provider.slug} provider={provider}
            onManage={() => setDialog({ kind: 'manage', slug: provider.slug })}
            onEdit={() => setDialog({ kind: 'edit', slug: provider.slug })}
            onToggle={(enabled) => void run(() => providersApi.update(provider.slug, { enabled }), enabled ? `${provider.name} 已启用` : `${provider.name} 已停用`)}
            onDelete={() => { if (confirm(`删除供应商 ${provider.name} 及其全部模型？`)) void run(() => providersApi.remove(provider.slug), '供应商已删除') }} />)}
        </div>}
    </> : <DefaultModels providers={providers} purposes={purposes} routes={routes} onChanged={load} notify={notify} />}

    {(dialog?.kind === 'create' || (dialog?.kind === 'edit' && target)) && <ProviderDialog types={types} editing={dialog?.kind === 'edit' ? target : undefined}
      onClose={() => setDialog(null)} onError={() => undefined}
      onSaved={async (provider, text) => { notify(true, text); await load(); setDialog(dialog?.kind === 'create' ? { kind: 'manage', slug: provider.slug } : null) }} />}
    {dialog?.kind === 'manage' && target && <ManageModelsDialog provider={target} notify={notify} onChanged={load} onClose={() => setDialog(null)} />}
  </div>
}
