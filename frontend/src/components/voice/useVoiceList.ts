import { useEffect, useState } from 'react'
import { nodeCanvasApi, type PresetVoice } from '../../api/nodeCanvas'

/** 预置音色是后端常量表，整个会话只取一次；取失败返回空列表且下次重试。 */
let cache: Promise<PresetVoice[]> | null = null

export function loadVoices(): Promise<PresetVoice[]> {
  if (!cache) {
    cache = Promise.resolve().then(() => nodeCanvasApi.voices()).catch(() => { cache = null; return [] as PresetVoice[] })
  }
  return cache
}

/** 仅测试用：清掉缓存。 */
export function resetVoiceCache() { cache = null }

export function useVoiceList(): PresetVoice[] {
  const [voices, setVoices] = useState<PresetVoice[]>([])
  useEffect(() => {
    let cancelled = false
    void loadVoices().then((value) => { if (!cancelled) setVoices(value) })
    return () => { cancelled = true }
  }, [])
  return voices
}

/** 音色在下拉里的显示名：芊悦 · 阳光积极。找不到（后端已下线的旧 id）时直接显示 id。 */
export function voiceLabel(voices: PresetVoice[], id: string): string {
  const voice = voices.find((item) => item.id === id)
  return voice ? voice.name : id
}
