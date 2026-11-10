import type { ThinkingLevel } from '../api/types'

/** 从左到右依次升高；也是滑块的吸附顺序。 */
export const THINKING_ORDER: ThinkingLevel[] = ['none', 'low', 'high', 'max']

/** 新模型的默认可选等级：除了 none 全选。 */
export const DEFAULT_THINKING_LEVELS: ThinkingLevel[] = ['low', 'high', 'max']

export const THINKING_OPTIONS: Array<{ value: ThinkingLevel; label: string; short: string }> = [
  { value: 'none', label: '不思考', short: '关' },
  { value: 'low', label: '低', short: '低' },
  { value: 'high', label: '高', short: '高' },
  { value: 'max', label: '最高', short: '最高' },
]

export function thinkingLabel(level: ThinkingLevel | string): string {
  return THINKING_OPTIONS.find((item) => item.value === level)?.label || '不思考'
}

export function thinkingShort(level: ThinkingLevel | string): string {
  return THINKING_OPTIONS.find((item) => item.value === level)?.short || '关'
}

/** 规范化可选等级并保证当前等级在可选集合里。 */
export function normalizeThinkingLevels(
  rawLevels: unknown,
  rawCurrent: unknown,
  fallback: ThinkingLevel[] = DEFAULT_THINKING_LEVELS,
): { levels: ThinkingLevel[]; thinking: ThinkingLevel } {
  const levels = THINKING_ORDER.filter((level) => (
    Array.isArray(rawLevels) && rawLevels.includes(level)
  ))
  const chosen = levels.length ? levels : [...fallback]
  const current = typeof rawCurrent === 'string' && chosen.includes(rawCurrent as ThinkingLevel)
    ? rawCurrent as ThinkingLevel
    : chosen[chosen.length - 1] || 'none'
  return { levels: chosen, thinking: current }
}

export function levelsSummary(levels: ThinkingLevel[]): string {
  return levels.length ? levels.map((level) => thinkingLabel(level)).join(' / ') : '未选择'
}
