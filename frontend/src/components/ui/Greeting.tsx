import './Greeting.css'

/** 首页问候语：按时间段 + 昵称 */
export function Greeting({ nickname }: { nickname: string }) {
  const h = new Date().getHours()
  const part = h < 6 ? '夜深了' : h < 12 ? '上午好' : h < 14 ? '中午好' : h < 18 ? '下午好' : '晚上好'
  return (
    <div className="greeting">
      <div className="greeting__title">
        {part}，{nickname} 👋
      </div>
      <div className="greeting__sub">今天想创作点什么？</div>
    </div>
  )
}
