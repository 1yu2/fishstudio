import { useEffect, useState } from 'react'
import type { Quote, RunRequest } from '../../api/nodeCanvas'

/**
 * 单节点报价：请求变化后防抖取价；enabled 为 false（有阻塞原因）时不报价。
 * 报价按请求指纹对应：请求一变，旧价格 / 旧错误立刻作废（loading 为 true），只有与当前请求一致的成功报价才算 ready。
 */
export function useQuote(quote: (request: RunRequest) => Promise<Quote>, request: RunRequest, enabled: boolean): {
  price: Quote | null; error: string; loading: boolean
} {
  const [state, setState] = useState<{ key: string; price: Quote | null; error: string } | null>(null)
  const key = JSON.stringify(request)
  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    const timer = window.setTimeout(() => {
      quote(JSON.parse(key) as RunRequest)
        .then((value) => { if (!cancelled) setState({ key, price: value, error: '' }) })
        .catch((reason) => { if (!cancelled) setState({ key, price: null, error: reason instanceof Error && reason.message ? reason.message : '暂时无法报价' }) })
    }, 400)
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [enabled, key, quote])
  const fresh = enabled && state?.key === key ? state : null
  return { price: fresh?.price ?? null, error: fresh?.error ?? '', loading: enabled && !fresh }
}
