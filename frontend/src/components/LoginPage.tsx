import { FormEvent, useState } from 'react'
import { Eye, EyeOff } from 'lucide-react'
import { login } from '../api'
import './LoginPage.css'

type Props = {
  onLoggedIn: () => void
}

export default function LoginPage({ onLoggedIn }: Props) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      await login(username.trim(), password)
      onLoggedIn()
    } catch (err) {
      setError(err instanceof Error ? err.message : '登录失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login">
      <div className="login__panel">
        <div className="login__brand">
          <img src="/logo.png" alt="FishStudio" className="login__logo" />
          <div className="login__brand-name">FishStudio</div>
          <div className="login__brand-slogan">创意不止于画布</div>
          <ul className="login__brand-points">
            <li>🖼 文生图 · 文生视频 · 3D 模型</li>
            <li>🧑‍🎤 虚拟人口型同步</li>
            <li>🎙 TTS 与音频混音</li>
          </ul>
        </div>
        <form className="login__card" onSubmit={onSubmit}>
          <h1 className="login__title">FishStudio</h1>
          <p className="login__subtitle">请使用管理员账号登录</p>

          <label className="login__field">
            <span className="login__label">用户名</span>
            <div className="login__input-wrap">
              <input
                type="text"
                className="login__input"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoFocus
                required
                autoComplete="username"
              />
            </div>
          </label>

          <label className="login__field">
            <span className="login__label">密码</span>
            <div className="login__input-wrap">
              <input
                type={showPassword ? 'text' : 'password'}
                className="login__input login__input--with-toggle"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete="current-password"
              />
              <button
                type="button"
                className="login__toggle"
                onClick={() => setShowPassword((v) => !v)}
                aria-label={showPassword ? '隐藏密码' : '显示密码'}
                title={showPassword ? '隐藏密码' : '显示密码'}
              >
                {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </label>

          {error && <div className="login__error">{error}</div>}

          <button
            type="submit"
            className="login__submit"
            disabled={loading || !username || !password}
          >
            {loading ? '登录中...' : '登录'}
          </button>

          <p className="login__hint">
            初始账号在 <code>backend/.env</code> 的 <code>ADMIN_USERNAME</code> /{' '}
            <code>ADMIN_PASSWORD</code> 配置
          </p>
        </form>
      </div>
    </div>
  )
}
