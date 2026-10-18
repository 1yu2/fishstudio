import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertCircle, Check, Plus, Search, X } from 'lucide-react'
import { ModelProvider, ModelRoute, ProviderPreset, PurposeDefinition, modelServicesApi, providersApi } from '../../modelServicesApi'
import DefaultModels from './DefaultModels'
import ProviderDetail from './ProviderDetail'
import './modelServices.css'

type View = 'providers' | 'defaults'

function statusTone(provider: ModelProvider): 'on' | 'off' | 'unset' {
  if (!provider.enabled) return 'off'
  return provider.credential_configured ? 'on' : 'unset'
}

export default function ModelServicesPanel() {
  const [view, setView] = useState<View>('providers')
  const [providers, setProviders] = useState<ModelProvider[]>([])
  const [presets, setPresets] = useState<ProviderPreset[]>([])
  const [routes, setRoutes] = useState<ModelRoute[]>([])
  const [purposes, setPurposes] = useState<PurposeDefinition[]>([])
  const [selected, setSelected] = useState<string>('')
  const [query, setQuery] = useState('')
  const [adding, setAdding] = useState(false)
  const [custom, setCustom] = useState({ name: '', baseUrl: '' })
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null)
  const [reloadError, setReloadError] = useState('')

  const notify = useCallback((ok: boolean, text: string) => setNotice({ ok, text }), [])

  const load = useCallback(async () => {
    try {
      const [providerRows, presetRows, routeRows, purposeRows, runtime] = await Promise.all([
        providersApi.list(), providersApi.presets(), modelServicesApi.routes(), modelServicesApi.purposes(), modelServicesApi.runtime(),
      ])
      setProviders(providerRows); setPresets(presetRows); setRoutes(routeRows); setPurposes(purposeRows)
      setReloadError(runtime.last_reload_failure?.reason || '')
      setSelected((current) => current && providerRows.some((item) => item.slug === current) ? current : providerRows[0]?.slug || '')
    } catch (error) { notify(false, error instanceof Error ? error.message : '加载失败') }
  }, [notify])

  useEffect(() => { void load() }, [load])

  const filtered = useMemo(() => {
    const keyword = query.trim().toLowerCase()
    if (!keyword) return providers
    return providers.filter((provider) => provider.name.toLowerCase().includes(keyword)
      || provider.models.some((model) => model.model.toLowerCase().includes(keyword) || model.name.toLowerCase().includes(keyword)))
  }, [providers, query])
  const current = providers.find((provider) => provider.slug === selected)

  const addProvider = async (preset: string, name = '', baseUrl = '') => {
    try {
      const created = await providersApi.create({ preset, name, base_url: baseUrl })
      setAdding(false); setCustom({ name: '', baseUrl: '' })
      notify(true, `已添加 ${created.name}，填写 API 密钥后点击「检查」`)
      await load()
      setSelected(created.slug)
    } catch (error) { notify(false, error instanceof Error ? error.message : '添加失败') }
  }

  return <div className="model-services">
    <div className="settings-panel__title">模型服务</div>
    <div className="ms-tabs" role="tablist">
      <button role="tab" aria-selected={view === 'providers'} className={view === 'providers' ? 'ms-tab ms-tab--on' : 'ms-tab'} onClick={() => setView('providers')}>模型供应商</button>
      <button role="tab" aria-selected={view === 'defaults'} className={view === 'defaults' ? 'ms-tab ms-tab--on' : 'ms-tab'} onClick={() => setView('defaults')}>默认模型</button>
    </div>
    {notice && <div className={`settings-toast settings-toast--${notice.ok ? 'success' : 'error'}`}>{notice.ok ? <Check size={14} /> : <AlertCircle size={14} />}{notice.text}</div>}
    {reloadError && <div className="model-reload-error"><AlertCircle size={14} />最近一次加载模型配置失败：{reloadError}</div>}

    {view === 'providers' ? <div className="ms-layout">
      <aside className="ms-sidebar" aria-label="供应商列表">
        <div className="ms-search"><Search size={14} /><input placeholder="搜索供应商或模型" value={query} onChange={(e) => setQuery(e.target.value)} /></div>
        <div className="ms-provider-list">
          {filtered.map((provider) => <button key={provider.slug} aria-current={provider.slug === selected ? 'true' : undefined}
            aria-label={`${provider.name}，${provider.models.length} 个模型，${{ on: '已配置', off: '已停用', unset: '缺少密钥' }[statusTone(provider)]}`}
            className={`ms-provider${provider.slug === selected ? ' ms-provider--on' : ''}`} onClick={() => setSelected(provider.slug)}>
            <span className="ms-provider__name">{provider.name}</span>
            <span className="ms-provider__count">{provider.models.length}</span>
            <span className={`ms-dot ms-dot--${statusTone(provider)}`} aria-hidden="true" />
          </button>)}
          {filtered.length === 0 && <div className="ms-empty">{providers.length ? '没有匹配的供应商' : '还没有供应商'}</div>}
        </div>
        <button className="ms-add-provider" onClick={() => setAdding(true)}><Plus size={14} />添加供应商</button>
      </aside>
      <div className="ms-main">
        {current ? <ProviderDetail key={current.slug} provider={current} notify={notify} onChanged={load} onDeleted={async () => { setSelected(''); await load() }} />
          : <div className="ms-empty ms-empty--center">添加一个供应商开始，例如火山方舟（Seedance）、DeepSeek 或自建 GPU。</div>}
      </div>
    </div> : <DefaultModels providers={providers} purposes={purposes} routes={routes} onChanged={load} notify={notify} />}

    {adding && <div className="ms-dialog" role="dialog" aria-label="添加供应商">
      <div className="ms-dialog__panel ms-dialog__panel--wide">
        <div className="ms-dialog__head"><strong>添加供应商</strong><button className="ms-icon-btn" aria-label="关闭" onClick={() => setAdding(false)}><X size={16} /></button></div>
        <div className="ms-preset-grid">
          {presets.filter((preset) => !preset.custom).map((preset) => {
            const existing = providers.find((provider) => provider.preset.id === preset.id)
            return <div key={preset.id} className="ms-preset">
              {/* 已添加的预设：点击跳到已有供应商，避免误建重复账号；确需第二个账号用下方按钮。 */}
              <button className="ms-preset__main" onClick={() => existing ? (setSelected(existing.slug), setAdding(false)) : void addProvider(preset.id)}>
                <strong>{preset.name}{existing && <span className="ms-tag">已添加</span>}</strong>
                <small>{preset.description}</small>
                <span className="ms-preset__tags">{preset.tags.map((tag) => <span className="ms-tag ms-tag--muted" key={tag}>{tag}</span>)}</span>
              </button>
              {existing && <button className="ms-preset__extra" onClick={() => void addProvider(preset.id)}>再添加一个账号</button>}
            </div>
          })}
        </div>
        <div className="ms-custom">
          <strong>自定义 OpenAI 兼容</strong>
          <div className="ms-field__row">
            <input className="settings-input" placeholder="名称，例如 公司网关" value={custom.name} onChange={(e) => setCustom({ ...custom, name: e.target.value })} />
            <input className="settings-input" placeholder="https://api.example.com/v1" value={custom.baseUrl} onChange={(e) => setCustom({ ...custom, baseUrl: e.target.value })} />
            <button className="settings-save-btn" disabled={!/^https?:\/\/[^/\s]+/.test(custom.baseUrl.trim())}
              onClick={() => void addProvider('openai-compatible', custom.name, custom.baseUrl.trim())}>添加</button>
          </div>
        </div>
      </div>
    </div>}
  </div>
}
