export interface StoryboardShot {
  index: number
  duration?: string
  tag?: string
  description: string
}

export interface Storyboard {
  title?: string
  shots: StoryboardShot[]
  style?: string
  tone?: string
}

const shotPattern = /^\s*(?:[-*]\s*)?镜头\s*(\d+)\s*(?:[（(]([^）)]+)[）)])?\s*(?:【([^】]+)】)?\s*[：:]\s*(.+?)\s*$/
const titlePattern = /(?:生成|制作|创作)(?:一个|一条)?([^，。\n]*视频)[，。\n]/

export const parseStoryboard = (content: string): Storyboard | null => {
  if (!content || !content.includes('镜头')) return null

  const lines = content.split(/\r?\n/)
  const shots: StoryboardShot[] = []
  let style: string | undefined
  let tone: string | undefined

  for (const line of lines) {
    const shotMatch = line.match(shotPattern)
    if (shotMatch) {
      shots.push({
        index: Number(shotMatch[1]),
        duration: shotMatch[2]?.trim(),
        tag: shotMatch[3]?.trim(),
        description: shotMatch[4].trim(),
      })
      continue
    }

    const styleMatch = line.match(/^\s*整体风格\s*[：:]\s*(.+?)\s*$/)
    if (styleMatch) {
      style = styleMatch[1].trim()
      continue
    }

    const toneMatch = line.match(/^\s*色调\s*[：:]\s*(.+?)\s*$/)
    if (toneMatch) {
      tone = toneMatch[1].trim()
    }
  }

  if (shots.length < 2) return null

  const title = content.match(titlePattern)?.[1]?.trim()
  return { title, shots, style, tone }
}
