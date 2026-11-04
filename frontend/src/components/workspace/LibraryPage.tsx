import { Users } from 'lucide-react'
import './WorkspacePage.css'

/** 角色库占位页；真正的角色库在后续步骤上线。 */
export default function LibraryPage() {
  return (
    <div className="ws">
      <header className="ws-head">
        <h1>角色库</h1>
      </header>
      <div className="ws-state ws-state--empty">
        <Users size={28} aria-hidden="true" />
        <h2>角色库还是空的</h2>
        <p>在项目里定稿的角色可以存进角色库，在其他项目直接使用（即将上线）。</p>
      </div>
    </div>
  )
}
