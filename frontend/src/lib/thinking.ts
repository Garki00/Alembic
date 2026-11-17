import type { ThinkingLevel } from '../api/types'

/** Ascending from left to right; also the slider's snap order. */
export const THINKING_ORDER: ThinkingLevel[] = ['none', 'low', 'high', 'max']

/** Default selectable levels for new models: all except none. */
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

/** Normalize the selectable levels and ensure the current level is among them. */
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
