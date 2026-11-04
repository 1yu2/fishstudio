import type { StepKey } from '../../api/workspace'

export interface StepMeta {
  key: StepKey
  label: string
  /** 底栏主按钮文案；成片是最后一步，没有下一步 */
  nextLabel: string | null
  /** 占位页的一句说明（取自需求 E7，对应步骤页做完后删除） */
  intro: string
}

export const STEPS: StepMeta[] = [
  {
    key: 'script',
    label: '剧本',
    nextLabel: '确认剧本，去定角色 →',
    intro: '左侧大纲（幕/场）、右侧正文编辑，可 AI 改写、缩短、换语气，也可粘贴已有剧本。',
  },
  {
    key: 'characters',
    label: '角色',
    nextLabel: '角色定稿，去分镜 →',
    intro: '本集角色 4 选 1 定稿、多套造型，右侧是跨项目复用的角色库，也可上传自有角色图。',
  },
  {
    key: 'storyboard',
    label: '分镜',
    nextLabel: '完成分镜，去配音 →',
    intro: '顶部故事板横条看每个镜头的状态与进度，下方是镜头详情，可批量生成并基于某张图继续修改。',
  },
  {
    key: 'voice',
    label: '配音',
    nextLabel: '配音完成，去成片 →',
    intro: '左侧选择角色音色并试听，右侧按镜头编辑台词；配音长于镜头时会提示延长镜头或缩短台词。',
  },
  {
    key: 'final',
    label: '成片',
    nextLabel: null,
    intro: '预览成片与只读多轨时间线，设置字幕、BGM 与导出规格，查看出片检查结果后导出。',
  },
]

export const STEP_KEYS = STEPS.map((step) => step.key)

export function stepMeta(key: string | undefined): StepMeta | undefined {
  return STEPS.find((step) => step.key === key)
}
