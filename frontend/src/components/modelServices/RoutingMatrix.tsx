import { Eraser } from 'lucide-react'
import { ModelDeployment, ModelRoute, PurposeDefinition, RouteTier, modelServicesApi } from '../../modelServicesApi'
import { PURPOSE_LABELS, TIER_LABELS, priceLabel } from './vendors'

const TIERS: RouteTier[] = ['draft', 'preview', 'final']
const MAX_FALLBACKS = 2

interface Props {
  purposes: PurposeDefinition[]
  routes: ModelRoute[]
  deployments: ModelDeployment[]
  onChanged: () => Promise<void>
  notify: (ok: boolean, text: string) => void
}

export default function RoutingMatrix({ purposes, routes, deployments, onChanged, notify }: Props) {
  const routeOf = (purpose: string, tier: RouteTier) => routes.find((route) => route.purpose === purpose && route.tier === tier)
  const bySlug = Object.fromEntries(deployments.map((item) => [item.slug, item]))

  const save = async (purpose: PurposeDefinition, tier: RouteTier, primary: string, fallbacks: string[]) => {
    try {
      if (!primary) {
        if (tier === 'final') return
        await modelServicesApi.removeRoute(purpose.purpose, tier)
      } else {
        const normalized = fallbacks.filter((slug, index) => slug && slug !== primary && fallbacks.indexOf(slug) === index)
        await modelServicesApi.saveRoute({
          purpose: purpose.purpose, tier, modality: purpose.modality,
          primary_deployment: primary, fallback_deployments: normalized, enabled: true,
        })
      }
      notify(true, `${PURPOSE_LABELS[purpose.purpose] || purpose.purpose} · ${TIER_LABELS[tier]} 已更新`)
      await onChanged()
    } catch (error) { notify(false, error instanceof Error ? error.message : '绑定失败') }
  }

  return <div className="routing-matrix">
    <h3>用途 × 档位</h3>
    <p>每个用途按档位选择主模型和备用模型。草稿 / 预览档留空时沿用终版；视频镜头会按真实请求形态（文生 / 首帧 / 首尾帧）跳过不支持、缺 Key 或未定价的候选。</p>
    <div className="routing-matrix__table" role="table" aria-label="用途档位路由">
      <div className="routing-matrix__row routing-matrix__row--head" role="row">
        <div role="columnheader">用途</div>
        {TIERS.map((tier) => <div role="columnheader" key={tier}>{TIER_LABELS[tier]}</div>)}
      </div>
      {purposes.map((purpose) => {
        const candidates = deployments.filter((item) => item.enabled && item.modality === purpose.modality)
        const finalRoute = routeOf(purpose.purpose, 'final')
        return <div className="routing-matrix__row" role="row" key={purpose.purpose}>
          <div role="rowheader">
            <strong>{PURPOSE_LABELS[purpose.purpose] || purpose.purpose}</strong>
            <small>{purpose.purpose}{purpose.required_capabilities.length ? ` · 需要 ${purpose.required_capabilities.join(', ')}` : ''}</small>
          </div>
          {TIERS.map((tier) => {
            const route = routeOf(purpose.purpose, tier)
            const primary = route?.primary_deployment || ''
            const fallbacks = route?.fallback_deployments || []
            const inherited = !route && tier !== 'final' && finalRoute
            const label = `${PURPOSE_LABELS[purpose.purpose] || purpose.purpose} ${TIER_LABELS[tier]}`
            return <div role="cell" className={`routing-cell${inherited ? ' routing-cell--inherited' : ''}`} key={tier}>
              <span className="routing-cell__tier">{TIER_LABELS[tier]}</span>
              <select className="settings-input" aria-label={`${label} 主模型`} value={primary}
                onChange={(e) => void save(purpose, tier, e.target.value, fallbacks)}>
                <option value="">{tier === 'final' ? '选择主模型' : '沿用终版'}</option>
                {candidates.map((item) => <option key={item.slug} value={item.slug}>{item.name}</option>)}
              </select>
              {primary && Array.from({ length: Math.min(MAX_FALLBACKS, fallbacks.length + 1) }, (_, index) =>
                <select key={index} className="settings-input routing-cell__fallback" aria-label={`${label} 备用 ${index + 1}`}
                  value={fallbacks[index] || ''}
                  onChange={(e) => {
                    const next = [...fallbacks]
                    if (e.target.value) next[index] = e.target.value
                    else next.splice(index, 1)
                    void save(purpose, tier, primary, next)
                  }}>
                  <option value="">备用 {index + 1}（可选）</option>
                  {candidates.filter((item) => item.slug !== primary).map((item) => <option key={item.slug} value={item.slug}>{item.name}</option>)}
                </select>)}
              <div className="routing-cell__hint">
                {primary ? priceLabel(bySlug[primary]?.pricing || {}) : inherited ? `→ ${bySlug[finalRoute.primary_deployment]?.name || finalRoute.primary_deployment}` : tier === 'final' ? '未配置' : ''}
                {route && tier !== 'final' && <button className="routing-cell__clear" aria-label={`清除 ${label}`} onClick={() => void save(purpose, tier, '', [])}><Eraser size={12} /></button>}
              </div>
            </div>
          })}
        </div>
      })}
    </div>
  </div>
}
