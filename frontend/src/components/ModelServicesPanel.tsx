import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertCircle, Check, ChevronDown, Plus, RefreshCw, Save, Trash2 } from 'lucide-react'
import { ModelDeployment, ModelRoute, PurposeDefinition, modelServicesApi } from '../modelServicesApi'

const DRIVER_OPTIONS = [
  ['local_h3_gateway', 'Local H3 Gateway', 'video'],
  ['openai_compatible', 'OpenAI 兼容', 'chat'],
  ['volcano_image', '火山图片', 'image'],
  ['minimax_video', 'MiniMax 视频', 'video'],
  ['volcano_video', '火山视频', 'video'],
  ['qwen_tts', 'Qwen TTS', 'speech'],
  ['tencent_ai3d', '腾讯 3D', 'model_3d'],
] as const

const CREDENTIAL_REFS: Record<string, string> = {
  local_h3_gateway: 'env:LOCAL_H3_API_KEY', openai_compatible: 'env:OPENAI_API_KEY',
  volcano_image: 'env:VOLCANO_API_KEY', volcano_video: 'env:VOLCANO_API_KEY',
  minimax_video: 'env:MINIMAX_API_KEY', qwen_tts: 'env:DASHSCOPE_API_KEY',
  tencent_ai3d: 'env:TENCENT_AI3D_API_KEY',
}

interface FormState {
  name: string; slug: string; driver: string; modality: string; model: string
  base_url: string; api_key: string; priority: number; config: string; capabilities: string; pricing: string
}

const emptyForm: FormState = {
  name: '', slug: '', driver: 'local_h3_gateway', modality: 'video', model: 'MiniMax-H3',
  base_url: '', api_key: '', priority: 100, config: '{}', capabilities: '{}', pricing: '{}',
}

function statusOf(item: ModelDeployment, available: boolean | undefined) {
  if (!item.enabled) return ['已停用', 'off']
  if (!item.credential_configured) return ['未配置', 'unset']
  if (available === false) return ['不可连接', 'error']
  return ['正常', 'on']
}

export default function ModelServicesPanel() {
  const [items, setItems] = useState<ModelDeployment[]>([])
  const [availability, setAvailability] = useState<Record<string, boolean>>({})
  const [routes, setRoutes] = useState<ModelRoute[]>([])
  const [purposes, setPurposes] = useState<PurposeDefinition[]>([])
  const [form, setForm] = useState<FormState>(emptyForm)
  const [editing, setEditing] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null)
  const [reloadError, setReloadError] = useState('')

  const load = useCallback(async () => {
    try {
      const [deployments, runtime, routeRows, purposeRows] = await Promise.all([
        modelServicesApi.deployments(), modelServicesApi.runtime(), modelServicesApi.routes(), modelServicesApi.purposes(),
      ])
      setItems(deployments)
      setAvailability(Object.fromEntries(runtime.items.map((item) => [item.slug, item.available])))
      setReloadError(runtime.last_reload_failure?.reason || '')
      setRoutes(routeRows); setPurposes(purposeRows)
    } catch (error) { setNotice({ ok: false, text: error instanceof Error ? error.message : '加载失败' }) }
  }, [])

  useEffect(() => { void load() }, [load])

  const edit = (item: ModelDeployment) => {
    setEditing(item.slug); setShowForm(true)
    setForm({
      name: item.name, slug: item.slug, driver: item.driver, modality: item.modality,
      model: item.model, base_url: item.base_url, api_key: '', priority: item.priority,
      config: JSON.stringify(item.config, null, 2), capabilities: JSON.stringify(item.capabilities, null, 2),
      pricing: JSON.stringify(item.pricing, null, 2),
    })
  }

  const save = async () => {
    setBusy(true); setNotice(null)
    try {
      const payload = {
        name: form.name, slug: form.slug, driver: form.driver, modality: form.modality,
        model: form.model, base_url: form.base_url, priority: form.priority,
        credential_ref: CREDENTIAL_REFS[form.driver], enabled: true,
        config: JSON.parse(form.config), capabilities: JSON.parse(form.capabilities), pricing: JSON.parse(form.pricing),
      }
      const saved = editing
        ? await modelServicesApi.update(editing, payload)
        : await modelServicesApi.create(payload)
      if (form.api_key.trim()) await modelServicesApi.credential(saved.slug, form.api_key)
      setNotice({ ok: true, text: '模型服务已保存并热加载' }); setShowForm(false); setEditing(null); setForm(emptyForm)
      await load()
    } catch (error) { setNotice({ ok: false, text: error instanceof Error ? error.message : '保存失败' }) }
    finally { setBusy(false) }
  }

  const deploymentsByModality = useMemo(() => Object.fromEntries(
    purposes.map((purpose) => [purpose.purpose, items.filter((item) => item.enabled && item.modality === purpose.modality)])
  ), [items, purposes])

  // 当前页面只编辑 final 档；draft/preview 档位的编辑随设置页改版一起提供。
  const routeFor = (purpose: PurposeDefinition) => routes.find((route) => route.purpose === purpose.purpose && route.tier === 'final')
  const saveBinding = async (purpose: PurposeDefinition, primary: string) => {
    if (!primary) return
    const current = routeFor(purpose)
    try {
      await modelServicesApi.saveRoute({
        purpose: purpose.purpose, tier: 'final', modality: purpose.modality, primary_deployment: primary,
        fallback_deployments: (current?.fallback_deployments || []).filter((slug) => slug !== primary), enabled: true,
      })
      await load(); setNotice({ ok: true, text: `${purpose.purpose} 用途已更新` })
    } catch (error) { setNotice({ ok: false, text: error instanceof Error ? error.message : '绑定失败' }) }
  }

  const saveFallback = async (purpose: PurposeDefinition, index: number, slug: string) => {
    const current = routeFor(purpose)
    if (!current?.primary_deployment) return
    const fallbacks = [...current.fallback_deployments]
    if (slug) fallbacks[index] = slug
    else fallbacks.splice(index, 1)
    const normalized = fallbacks.filter((value, position) =>
      value && value !== current.primary_deployment && fallbacks.indexOf(value) === position
    )
    try {
      await modelServicesApi.saveRoute({
        purpose: purpose.purpose, tier: 'final', modality: purpose.modality,
        primary_deployment: current.primary_deployment,
        fallback_deployments: normalized, enabled: true,
      })
      await load()
    } catch (error) { setNotice({ ok: false, text: error instanceof Error ? error.message : '备用模型保存失败' }) }
  }

  return <div className="model-services">
    <div className="settings-panel__title">模型服务</div>
    <div className="settings-panel__desc">统一管理 Chat、图片、视频、语音和 3D 服务。密钥仅可写入，不会返回到页面。</div>
    <div className="model-services__toolbar">
      <button className="settings-save-btn" onClick={() => { setEditing(null); setForm(emptyForm); setShowForm(true) }}><Plus size={15} />新增服务</button>
      <button className="model-action" onClick={() => void load()}><RefreshCw size={14} />刷新状态</button>
    </div>
    {notice && <div className={`settings-toast settings-toast--${notice.ok ? 'success' : 'error'}`}>{notice.ok ? <Check size={14} /> : <AlertCircle size={14} />}{notice.text}</div>}
    {reloadError && <div className="model-reload-error"><AlertCircle size={14} />最近一次加载失败：{reloadError}</div>}

    {showForm && <div className="model-form">
      <div className="model-form__grid">
        <label>名称<input className="settings-input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
        <label>稳定标识<input className="settings-input" disabled={!!editing} value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value.toLowerCase() })} /></label>
        <label>接入类型<select className="settings-input" disabled={!!editing} value={form.driver} onChange={(e) => { const option = DRIVER_OPTIONS.find(([driver]) => driver === e.target.value)!; setForm({ ...form, driver: option[0], modality: option[2] }) }}>{DRIVER_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>模型<input className="settings-input" value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} /></label>
        <label className="model-form__wide">服务地址<input className="settings-input" value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} /></label>
        <label className="model-form__wide">API Key（留空保持不变）<input type="password" autoComplete="new-password" className="settings-input" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} /></label>
      </div>
      <details><summary><ChevronDown size={14} />高级设置</summary>
        <label>配置 JSON<textarea className="settings-textarea" value={form.config} onChange={(e) => setForm({ ...form, config: e.target.value })} /></label>
        <label>能力 JSON<textarea className="settings-textarea" value={form.capabilities} onChange={(e) => setForm({ ...form, capabilities: e.target.value })} /></label>
        <label>成本 JSON（未配置显示 unavailable）<textarea className="settings-textarea" value={form.pricing} onChange={(e) => setForm({ ...form, pricing: e.target.value })} /></label>
      </details>
      <button className="settings-save-btn" disabled={busy} onClick={() => void save()}><Save size={14} />{busy ? '保存中…' : '保存'}</button>
    </div>}

    <div className="runtime-model-grid">
      {items.map((item) => { const status = statusOf(item, availability[item.slug]); return <div className="runtime-model-card" key={item.slug}>
        <div className="runtime-model-card__title"><span>{item.name}</span><span className={`runtime-model-card__state runtime-model-card__state--${status[1]}`}>{status[0]}</span></div>
        <div className="runtime-model-card__meta">{item.modality} · {DRIVER_OPTIONS.find(([driver]) => driver === item.driver)?.[1] || item.driver}</div>
        <div className="runtime-model-card__model">{item.model}</div>
        <div className="model-address">{item.base_url.replace(/:\/\/([^/]+).*/, '://$1')}</div>
        <div className="model-card-actions">
          <button className="model-action" onClick={() => edit(item)}>编辑</button>
          <button className="model-action" onClick={async () => { try { await modelServicesApi.health(item.slug); setNotice({ ok: true, text: `${item.name} 连接正常` }) } catch (error) { setNotice({ ok: false, text: error instanceof Error ? error.message : '不可连接' }) } }}>测试连接</button>
          <button className="model-action" onClick={async () => { await modelServicesApi.enable(item.slug, !item.enabled); await load() }}>{item.enabled ? '停用' : '启用'}</button>
          <button className="model-action model-action--danger" onClick={async () => { if (!confirm(`确定删除 ${item.name}？`)) return; try { await modelServicesApi.remove(item.slug); await load() } catch (error) { setNotice({ ok: false, text: error instanceof Error ? error.message : '删除失败' }) } }}><Trash2 size={13} /></button>
        </div>
      </div> })}
    </div>

    <div className="model-routes"><h3>用途绑定</h3><p>为每个业务用途选择主模型；备用顺序由后端安全回退策略控制。</p>
      {purposes.map((purpose) => { const current = routeFor(purpose); const candidates = deploymentsByModality[purpose.purpose] || []; return <div className="model-route-row" key={purpose.purpose}>
        <div><strong>{purpose.purpose}</strong><small>{purpose.modality}{purpose.required_capabilities.length ? ` · 需要 ${purpose.required_capabilities.join(', ')}` : ''}</small></div>
        <div className="model-route-selects">
          <select className="settings-input" value={current?.primary_deployment || ''} onChange={(e) => void saveBinding(purpose, e.target.value)}>
            <option value="">选择主模型</option>{candidates.map((item) => <option key={item.slug} value={item.slug}>{item.name}</option>)}
          </select>
          {[0, 1].map((index) => <select key={index} className="settings-input" disabled={!current} value={current?.fallback_deployments[index] || ''} onChange={(e) => void saveFallback(purpose, index, e.target.value)}>
            <option value="">备用 {index + 1}（可选）</option>{candidates.filter((item) => item.slug !== current?.primary_deployment).map((item) => <option key={item.slug} value={item.slug}>{item.name}</option>)}
          </select>)}
        </div>
      </div> })}
    </div>
  </div>
}
