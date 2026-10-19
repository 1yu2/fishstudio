import { Pencil, SlidersHorizontal, Trash2 } from 'lucide-react'
import { ModelProvider } from '../../modelServicesApi'
import { Switch } from './ManageModelsDialog'
import { TYPE_BADGES } from './labels'

interface Props {
  provider: ModelProvider
  onManage: () => void
  onEdit: () => void
  onDelete: () => void
  onToggle: (enabled: boolean) => void
}

export function statusOf(provider: ModelProvider): { tone: 'on' | 'off' | 'unset'; label: string } {
  if (!provider.enabled) return { tone: 'off', label: '已停用' }
  if (!provider.credential_configured) return { tone: 'unset', label: `缺少 ${provider.api_key_env}` }
  return { tone: 'on', label: '可用' }
}

export default function ProviderCard({ provider, onManage, onEdit, onDelete, onToggle }: Props) {
  const badge = TYPE_BADGES[provider.provider_type.id] || { text: provider.name.slice(0, 2).toUpperCase(), color: '#475569' }
  const status = statusOf(provider)
  const enabledModels = provider.models.filter((model) => model.enabled).length
  return <article className={`pv-card${provider.enabled ? '' : ' pv-card--off'}`} aria-label={provider.name}>
    <header className="pv-card__head">
      <span className="pv-card__icon" style={{ background: badge.color }} aria-hidden="true">{badge.text}</span>
      <div className="pv-card__title">
        <strong>{provider.name}</strong>
        <code>{provider.slug}</code>
      </div>
      <div className="pv-card__tools">
        <button className="ms-icon-btn" aria-label={`编辑 ${provider.name}`} onClick={onEdit}><Pencil size={14} /></button>
        <button className="ms-icon-btn ms-icon-btn--danger" aria-label={`删除 ${provider.name}`} onClick={onDelete}><Trash2 size={14} /></button>
      </div>
      <span className={`ms-dot ms-dot--${status.tone}`} role="img" aria-label={status.label} title={status.label} />
    </header>
    <dl className="pv-card__rows">
      <dt>Base URL</dt><dd title={provider.base_url}>{provider.base_url || '—'}</dd>
      <dt>能力</dt><dd>{provider.capabilities.join(', ') || '—'}</dd>
    </dl>
    <footer className="pv-card__foot">
      <button className="pv-card__manage" onClick={onManage}><SlidersHorizontal size={14} />管理模型 <span>（已启用 {enabledModels} 个）</span></button>
      <Switch label={`启用 ${provider.name}`} checked={provider.enabled} onChange={onToggle} />
    </footer>
  </article>
}
