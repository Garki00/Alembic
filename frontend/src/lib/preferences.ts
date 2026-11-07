import type { CollapseTiming, TranscriptViewMode } from '../api/types'

export interface TranscriptModeOption {
  value: TranscriptViewMode
  label: string
  hint: string
}

export const TRANSCRIPT_MODES: TranscriptModeOption[] = [
  { value: 'compact', label: '简洁', hint: '只显示步骤摘要与错误' },
  { value: 'standard', label: '标准', hint: '显示工具名与简短结果' },
  { value: 'detailed', label: '详细', hint: '显示参数与较完整结果' },
  { value: 'verbose', label: '完全展开', hint: '始终展开全部步骤与输出' },
]

export const COLLAPSE_TIMINGS: Array<{ value: CollapseTiming; label: string; hint: string }> = [
  { value: 'completion', label: '回答结束后', hint: '本轮完成时自动收起工作步骤' },
  { value: 'next_input', label: '下次有新消息时', hint: '保留到下一次发送消息前' },
]
