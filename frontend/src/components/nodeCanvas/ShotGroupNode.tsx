import { memo } from 'react'
import type { NodeProps } from '@xyflow/react'
import { ChevronsDownUp } from 'lucide-react'
import { ProtectedImage } from '../common/ProtectedMedia'
import type { ShotGroup } from './groups'

export interface ShotGroupData extends Record<string, unknown> {
  group: ShotGroup
  collapsed: boolean
  onToggle: (shotId: string) => void
}

/** “镜头 3 · 近景 · 4 秒” */
export function groupHeading(group: ShotGroup): string {
  return [`镜头 ${group.index + 1}`, group.shotSize, group.duration ? `${group.duration} 秒` : ''].filter(Boolean).join(' · ')
}

/**
 * 镜头分组节点（派生，不保存）。展开：半透明圆角框 + 标题栏，框本身不拦截鼠标，只有标题栏可拖动 / 点折叠；
 * 折叠：缩略图 + 镜头名 + 状态的小卡片，点击展开。
 */
export const ShotGroupNode = memo(function ShotGroupNode({ data }: NodeProps) {
  const { group, collapsed, onToggle } = data as ShotGroupData
  if (collapsed) {
    return <div className={`nc-group-card nc-group-card--${group.state}`} role="button" tabIndex={0} aria-label={`展开镜头 ${group.index + 1}`} title={group.title}
      onClick={() => onToggle(group.shotId)}
      onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onToggle(group.shotId) } }}>
      <span className="nc-group-card__thumb">
        {group.thumbnail ? <ProtectedImage src={group.thumbnail.url} alt="" /> : group.state === 'failed' ? '!' : null}
      </span>
      <span className="nc-group-card__text">
        <b>{[`镜头 ${group.index + 1}`, group.shotSize].filter(Boolean).join(' · ')}</b>
        <span>{group.status}</span>
      </span>
    </div>
  }
  return <section className={`nc-group nc-group--${group.state}`} aria-label={`镜头 ${group.index + 1}（已展开）`}>
    <header className="nc-group__head">
      <span className="nc-group__title" title={group.title}>{groupHeading(group)}</span>
      <span className="nc-group__right">
        <span className={`nc-group__state nc-group__state--${group.state}`}>{group.status}</span>
        <button type="button" className="nc-group__fold nodrag" aria-label={`折叠镜头 ${group.index + 1}`} onClick={() => onToggle(group.shotId)}>
          <ChevronsDownUp size={12} />折叠
        </button>
      </span>
    </header>
  </section>
})
