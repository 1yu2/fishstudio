/** 复制到镜像元素上的样式：保证换行位置和文本框里一致 */
const MIRROR_PROPS = [
  'boxSizing', 'width', 'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft', 'borderTopWidth', 'borderRightWidth', 'borderBottomWidth', 'borderLeftWidth',
  'fontFamily', 'fontSize', 'fontWeight', 'fontStyle', 'letterSpacing', 'lineHeight', 'textIndent', 'textTransform', 'wordSpacing', 'tabSize',
] as const

/**
 * 文本框里第 index 个字符所在位置，相对文本框的 offsetParent（左上角）。
 * textarea 没有选区矩形可用，所以用一个看不见的镜像元素排版同样的文字，量标记点的位置。
 */
export function caretPosition(el: HTMLTextAreaElement, index: number): { top: number; left: number } {
  const mirror = document.createElement('div')
  const style = getComputedStyle(el)
  for (const prop of MIRROR_PROPS) mirror.style[prop] = style[prop]
  Object.assign(mirror.style, { position: 'absolute', visibility: 'hidden', top: '0', left: '-9999px', whiteSpace: 'pre-wrap', overflowWrap: 'break-word' })
  mirror.textContent = el.value.slice(0, index)
  const marker = document.createElement('span')
  marker.textContent = el.value.slice(index) || '.'
  mirror.appendChild(marker)
  document.body.appendChild(mirror)
  const result = { top: el.offsetTop + marker.offsetTop, left: el.offsetLeft + marker.offsetLeft }
  mirror.remove()
  return result
}

/** 工具条高度与它和选区之间的间距 */
export const BAR_HEIGHT = 34
export const BAR_GAP = 6

/**
 * 工具条的视口 top：优先放在选区起点所在行上方；上方空间不够（行靠近可视区顶部）就放到该行下方；
 * 最后夹进可视区 [boundsTop, boundsBottom]，选区起点已滚出可视区时工具条也留在边缘可见处。
 */
export function placeBar(lineTop: number, lineHeight: number, boundsTop: number, boundsBottom: number): number {
  const above = lineTop - BAR_HEIGHT - BAR_GAP
  const wanted = above >= boundsTop ? above : lineTop + lineHeight + BAR_GAP
  return Math.max(boundsTop, Math.min(wanted, boundsBottom - BAR_HEIGHT))
}

/** 页面实际的滚动容器（项目框架里的 .pf-body）；不在项目框架里时为 null，用整个窗口 */
export function scrollParent(el: Element): Element | null {
  return el.closest('.pf-body')
}
