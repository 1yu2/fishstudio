/** 相对时间：刚刚 / N 分钟前 / N 小时前 / N 天前，超过 30 天显示日期。 */
export function relativeTime(iso: string | null, now = Date.now()): string {
  if (!iso) return ''
  const diff = Math.max(0, now - new Date(iso).getTime())
  const minutes = Math.floor(diff / 60_000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  if (days <= 30) return `${days} 天前`
  return new Date(iso).toLocaleDateString()
}

/** 上限金额去掉多余的小数：50.00 → ¥50，12.5 → ¥12.5 */
export function yuanCap(value: string): string {
  return `¥${Number(value)}`
}

export function yuan(value: string): string {
  return `¥${value}`
}

const COVER_COLORS = ['#e0e7ff', '#ccfbf1', '#fde2d3', '#fef3c7', '#fce7f3', '#dcfce7']

/** 没有封面图时的色块：同一画布颜色稳定。 */
export function coverColor(id: string): string {
  let hash = 0
  for (const ch of id) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0
  return COVER_COLORS[hash % COVER_COLORS.length]
}
