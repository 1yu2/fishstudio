import './Avatar.css'

export const AVATARS: Array<{ id: string; emoji: string }> = [
  { id: 'fish', emoji: '🐟' },
  { id: 'fox', emoji: '🦊' },
  { id: 'panda', emoji: '🐼' },
  { id: 'lion', emoji: '🦁' },
  { id: 'rabbit', emoji: '🐰' },
  { id: 'whale', emoji: '🐳' },
  { id: 'chick', emoji: '🐣' },
]

/** avatarId 为 'letter' 或未知 id 时，用昵称首字符 + 渐变底 */
export function Avatar({
  avatarId,
  nickname,
  size = 28,
}: {
  avatarId: string
  nickname: string
  size?: number
}) {
  const emoji = AVATARS.find((a) => a.id === avatarId)?.emoji
  return (
    <span
      className={`avatar${emoji ? '' : ' avatar--letter'}`}
      style={{ width: size, height: size, fontSize: Math.round(size * 0.52) }}
    >
      {emoji ?? (nickname.trim().charAt(0) || '鱼')}
    </span>
  )
}
