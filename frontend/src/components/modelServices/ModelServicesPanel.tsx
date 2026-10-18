import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertCircle, Check, Plus, RefreshCw } from 'lucide-react'
import { ModelDeployment, ModelRoute, PurposeDefinition, modelServicesApi } from '../../modelServicesApi'
import ModelForm from './ModelForm'
import RoutingMatrix from './RoutingMatrix'
import VendorCard from './VendorCard'
import { VENDOR_PRESETS, Vendor, customVendor, groupByVendor } from './vendors'
import './modelServices.css'

interface FormTarget {
  vendor: Vendor
  editing?: ModelDeployment
}

export default function ModelServicesPanel() {
  const [items, setItems] = useState<ModelDeployment[]>([])
  const [availability, setAvailability] = useState<Record<string, boolean>>({})
  const [routes, setRoutes] = useState<ModelRoute[]>([])
  const [purposes, setPurposes] = useState<PurposeDefinition[]>([])
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null)
  const [reloadError, setReloadError] = useState('')
  const [formTarget, setFormTarget] = useState<FormTarget | null>(null)
  const [pickingVendor, setPickingVendor] = useState(false)
  const [custom, setCustom] = useState({ name: '', baseUrl: '' })
  // 尚未有模型、但用户刚选择接入的厂商，先显示为空卡片
  const [pendingVendors, setPendingVendors] = useState<Vendor[]>([])

  const notify = useCallback((ok: boolean, text: string) => setNotice({ ok, text }), [])

  const load = useCallback(async () => {
    try {
      const [deployments, runtime, routeRows, purposeRows] = await Promise.all([
        modelServicesApi.deployments(), modelServicesApi.runtime(), modelServicesApi.routes(), modelServicesApi.purposes(),
      ])
      setItems(deployments)
      setAvailability(Object.fromEntries(runtime.items.map((item) => [item.slug, item.available])))
      setReloadError(runtime.last_reload_failure?.reason || '')
      setRoutes(routeRows)
      setPurposes(purposeRows)
    } catch (error) { notify(false, error instanceof Error ? error.message : '加载失败') }
  }, [notify])

  useEffect(() => { void load() }, [load])

  const groups = useMemo(() => {
    const existing = groupByVendor(items)
    const shown = new Set(existing.map((group) => group.vendor.id))
    return [...existing, ...pendingVendors.filter((vendor) => !shown.has(vendor.id)).map((vendor) => ({ vendor, items: [] }))]
  }, [items, pendingVendors])
  const shownIds = new Set(groups.map((group) => group.vendor.id))

  const startVendor = (vendor: Vendor) => {
    setPendingVendors((current) => current.some((item) => item.id === vendor.id) ? current : [...current, vendor])
    setPickingVendor(false)
    setFormTarget({ vendor })
  }

  return <div className="model-services">
    <div className="settings-panel__title">模型服务</div>
    <div className="settings-panel__desc">先接入厂商（一个账号 / 一把 Key），再在厂商下添加模型；最后在下方为每个用途按档位绑定模型。密钥只写入，不会返回到页面。</div>
    <div className="model-services__toolbar">
      <button className="settings-save-btn" onClick={() => setPickingVendor(!pickingVendor)}><Plus size={15} />接入厂商</button>
      <button className="model-action" onClick={() => void load()}><RefreshCw size={14} />刷新状态</button>
    </div>
    {notice && <div className={`settings-toast settings-toast--${notice.ok ? 'success' : 'error'}`}>{notice.ok ? <Check size={14} /> : <AlertCircle size={14} />}{notice.text}</div>}
    {reloadError && <div className="model-reload-error"><AlertCircle size={14} />最近一次加载失败：{reloadError}</div>}

    {pickingVendor && <div className="vendor-picker" aria-label="选择厂商">
      {VENDOR_PRESETS.filter((vendor) => !shownIds.has(vendor.id)).map((vendor) => <button key={vendor.id} className="vendor-picker__item" onClick={() => startVendor(vendor)}>
        <strong>{vendor.name}</strong><small>{vendor.description}</small>
      </button>)}
      <div className="vendor-picker__custom">
        <strong>自定义 OpenAI 兼容厂商</strong>
        <input className="settings-input" placeholder="名称，如 DeepSeek" value={custom.name} onChange={(e) => setCustom({ ...custom, name: e.target.value })} />
        <input className="settings-input" placeholder="https://api.deepseek.com/v1" value={custom.baseUrl} onChange={(e) => setCustom({ ...custom, baseUrl: e.target.value })} />
        <button className="model-action" disabled={!/^https?:\/\/[^/]+/.test(custom.baseUrl.trim())}
          onClick={() => { startVendor(customVendor(custom.baseUrl.trim(), custom.name)); setCustom({ name: '', baseUrl: '' }) }}>接入</button>
      </div>
    </div>}

    {formTarget && <ModelForm
      key={`${formTarget.vendor.id}:${formTarget.editing?.slug || 'new'}`}
      vendor={formTarget.vendor}
      editing={formTarget.editing}
      onCancel={() => setFormTarget(null)}
      onError={(text) => notify(false, text)}
      onSaved={async (text) => { notify(true, text); setFormTarget(null); await load() }}
    />}

    <div className="vendor-list">
      {groups.length === 0 && <div className="vendor-card__empty">还没有接入任何厂商。点击「接入厂商」开始，例如火山方舟（Seedance）或自建 GPU。</div>}
      {groups.map((group) => <VendorCard
        key={group.vendor.id}
        vendor={group.vendor}
        items={group.items}
        availability={availability}
        notify={notify}
        onChanged={load}
        onAddModel={() => setFormTarget({ vendor: group.vendor })}
        onEdit={(item) => setFormTarget({ vendor: group.vendor, editing: item })}
      />)}
    </div>

    <RoutingMatrix purposes={purposes} routes={routes} deployments={items} onChanged={load} notify={notify} />
  </div>
}
