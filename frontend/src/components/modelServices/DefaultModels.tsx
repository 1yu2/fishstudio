import { useState } from 'react'
import { ModelProvider, ModelRoute, PurposeDefinition, RouteTier, modelServicesApi } from '../../modelServicesApi'
import { PURPOSE_LABELS, TIER_LABELS, priceLabel, supportsCapabilities } from './labels'

interface Props {
  providers: ModelProvider[]
  purposes: PurposeDefinition[]
  routes: ModelRoute[]
  onChanged: () => Promise<void>
  notify: (ok: boolean, text: string) => void
}

interface Option { slug: string; label: string; price: string }

// 默认只展示每个用途的终版；视频镜头额外展示预览档。草稿档与备用模型收在「高级」里。
function visibleTiers(purpose: string, advanced: boolean): RouteTier[] {
  if (advanced) return ['draft', 'preview', 'final']
  return purpose === 'shot_video' ? ['preview', 'final'] : ['final']
}

export default function DefaultModels({ providers, purposes, routes, onChanged, notify }: Props) {
  const [advanced, setAdvanced] = useState(false)

  const optionsFor = (purpose: PurposeDefinition): Option[] => providers
    .filter((provider) => provider.enabled)
    .flatMap((provider) => provider.models
      .filter((model) => model.enabled && model.modality === purpose.modality && supportsCapabilities(model.capabilities, purpose.required_capabilities))
      .map((model) => ({ slug: model.slug, label: `${provider.name} · ${model.name}`, price: priceLabel(model.pricing, model.modality) })))

  const routeOf = (purpose: string, tier: RouteTier) => routes.find((route) => route.purpose === purpose && route.tier === tier)

  const save = async (purpose: PurposeDefinition, tier: RouteTier, primary: string, fallbacks: string[]) => {
    const name = `${PURPOSE_LABELS[purpose.purpose]?.label || purpose.purpose}${tier === 'final' && purpose.purpose !== 'shot_video' ? '' : ` · ${TIER_LABELS[tier]}`}`
    try {
      if (!primary) {
        if (tier === 'final') return
        await modelServicesApi.removeRoute(purpose.purpose, tier)
      } else {
        await modelServicesApi.saveRoute({
          purpose: purpose.purpose, tier, modality: purpose.modality, primary_deployment: primary,
          fallback_deployments: fallbacks.filter((slug, index) => slug && slug !== primary && fallbacks.indexOf(slug) === index),
          enabled: true,
        })
      }
      notify(true, `${name} 已更新`)
      await onChanged()
    } catch (error) { notify(false, error instanceof Error ? error.message : '保存失败') }
  }

  return <div className="ms-defaults">
    <div className="ms-defaults__intro">
      <p>为每个用途选择默认使用的模型。只会列出已启用、且能力满足该用途的模型。</p>
      <label className="ms-check"><input type="checkbox" checked={advanced} onChange={(e) => setAdvanced(e.target.checked)} />高级：草稿档与备用模型</label>
    </div>
    {purposes.map((purpose) => {
      const options = optionsFor(purpose)
      const finalRoute = routeOf(purpose.purpose, 'final')
      const meta = PURPOSE_LABELS[purpose.purpose]
      return visibleTiers(purpose.purpose, advanced).map((tier) => {
        const route = routeOf(purpose.purpose, tier)
        const primary = route?.primary_deployment || ''
        const fallbacks = route?.fallback_deployments || []
        const inheritsFinal = tier !== 'final' && !route
        const title = `${meta?.label || purpose.purpose}${tier === 'final' && !advanced && purpose.purpose !== 'shot_video' ? '' : ` · ${TIER_LABELS[tier]}`}`
        const current = options.find((option) => option.slug === primary)
        return <div className="ms-default-row" key={`${purpose.purpose}:${tier}`}>
          <div className="ms-default-row__label">
            <strong>{title}</strong>
            <small>{inheritsFinal ? `未单独设置，沿用终版${finalRoute ? '' : '（终版也未设置）'}` : meta?.hint}</small>
          </div>
          <div className="ms-default-row__controls">
            <select className="settings-input" aria-label={`${title} 模型`} value={primary}
              onChange={(e) => void save(purpose, tier, e.target.value, fallbacks)}>
              <option value="">{tier === 'final' ? (options.length ? '选择模型' : '没有可用模型，请先在供应商中添加') : '沿用终版'}</option>
              {options.map((option) => <option key={option.slug} value={option.slug}>{option.label}</option>)}
            </select>
            {advanced && primary && [0, 1].map((index) => <select key={index} className="settings-input ms-fallback" aria-label={`${title} 备用 ${index + 1}`}
              value={fallbacks[index] || ''}
              onChange={(e) => { const next = [...fallbacks]; if (e.target.value) next[index] = e.target.value; else next.splice(index, 1); void save(purpose, tier, primary, next) }}>
              <option value="">备用 {index + 1}（可选）</option>
              {options.filter((option) => option.slug !== primary).map((option) => <option key={option.slug} value={option.slug}>{option.label}</option>)}
            </select>)}
            {current && <span className="ms-default-row__price">{current.price}</span>}
          </div>
        </div>
      })
    })}
  </div>
}
