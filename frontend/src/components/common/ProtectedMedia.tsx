/**
 * 受保护媒体组件（review P0-3 前端配合）。
 *
 * - src 为 /api/...（如 /api/artifacts/{id}/download）时：带 token fetch → blob
 *   → object URL 渲染，unmount 时 revokeObjectURL。
 * - src 为 /storage/... 等直链时：fallback 拼 ?token=<token> 直接渲染（老数据兼容）。
 * 含加载中 / 失败态。
 */
import {
  forwardRef,
  useEffect,
  useState,
  type AudioHTMLAttributes,
  type ImgHTMLAttributes,
  type VideoHTMLAttributes,
} from 'react'
import { acquireMediaUrl, isProtectedMediaUrl, legacyStorageUrl } from '../../api/media'

type MediaState = 'loading' | 'ready' | 'error'

function useProtectedSrc(src: string | undefined): { resolved: string | undefined; state: MediaState } {
  const [resolved, setResolved] = useState<string | undefined>(undefined)
  const [state, setState] = useState<MediaState>('loading')

  useEffect(() => {
    if (!src) {
      setResolved(undefined)
      setState('loading')
      return
    }
    if (!isProtectedMediaUrl(src)) {
      // 老数据 /storage/... 直链：拼 token fallback，不走 blob
      setResolved(legacyStorageUrl(src))
      setState('ready')
      return
    }
    let cancelled = false
    setResolved(undefined)
    setState('loading')
    const lease = acquireMediaUrl(src)
    lease.promise
      .then((objectUrl) => {
        if (cancelled) return
        setResolved(objectUrl)
        setState('ready')
      })
      .catch(() => {
        if (!cancelled) setState('error')
      })
    return () => {
      cancelled = true
      lease.release()
    }
  }, [src])

  return { resolved, state }
}

export const ProtectedVideo = forwardRef<HTMLVideoElement, { src?: string } & Omit<VideoHTMLAttributes<HTMLVideoElement>, 'src'>>(({
  src,
  ...rest
}, ref) => {
  const { resolved, state } = useProtectedSrc(src)
  if (state === 'loading') return <div className="protected-media protected-media--loading">加载中…</div>
  if (state === 'error' || !resolved) return <div className="protected-media protected-media--error">媒体加载失败</div>
  return <video ref={ref} src={resolved} {...rest} />
})
ProtectedVideo.displayName = 'ProtectedVideo'

export function ProtectedImage({
  src,
  alt = '',
  ...rest
}: { src?: string; alt?: string } & Omit<ImgHTMLAttributes<HTMLImageElement>, 'src' | 'alt'>) {
  const { resolved, state } = useProtectedSrc(src)
  if (state === 'loading') return <div className="protected-media protected-media--loading">加载中…</div>
  if (state === 'error' || !resolved) return <div className="protected-media protected-media--error">媒体加载失败</div>
  return <img src={resolved} alt={alt} {...rest} />
}

export function ProtectedAudio({
  src,
  ...rest
}: { src?: string } & Omit<AudioHTMLAttributes<HTMLAudioElement>, 'src'>) {
  const { resolved, state } = useProtectedSrc(src)
  if (state === 'loading') return <div className="protected-media protected-media--loading">加载中…</div>
  if (state === 'error' || !resolved) return <div className="protected-media protected-media--error">媒体加载失败</div>
  return <audio src={resolved} {...rest} />
}
