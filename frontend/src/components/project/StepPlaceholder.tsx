import { Link, useParams } from 'react-router-dom'
import { paths } from '../../routePaths'
import { stepMeta } from './steps'

/** 步骤页占位：各步骤的真实内容在后续任务里逐页替换；目前引导用户回到画布继续。 */
export default function StepPlaceholder({ step }: { step: string }) {
  const { canvasId = '' } = useParams()
  const meta = stepMeta(step)
  if (!meta) return null
  return (
    <section className="pf-placeholder" aria-label={meta.label}>
      <h1>{meta.label}</h1>
      <p>{meta.intro}</p>
      <p className="pf-placeholder__note">这一步的页面还在建设中。</p>
      <Link to={paths.projectCanvas(canvasId)} className="pf-placeholder__button">
        在画布中继续
      </Link>
    </section>
  )
}
