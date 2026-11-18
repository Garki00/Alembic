/** Relative time labels for the session list. */
export function relativeTime(value: string): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const diff = Date.now() - date.getTime()
  if (diff < 0) return '刚刚'
  const minutes = Math.floor(diff / 60_000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes}分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}小时前`
  const days = Math.floor(hours / 24)
  return `${days}天前`
}

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

/**
 * 分级时间：当天只显示时刻，同一年显示月日 + 时刻，跨年再带上年份。
 *
 * 2026-11-18 13:35（当天）→ 「13:35」
 * 2026-11-17 13:35（同年）→ 「11月17日 13:35」
 * 2025-11-17 13:35（跨年）→ 「2025年11月17日 13:35」
 */
export function formatStamp(ms?: number, now: Date = new Date()): string {
  if (!ms || !Number.isFinite(ms)) return ''
  const date = new Date(ms)
  const time = `${pad(date.getHours())}:${pad(date.getMinutes())}`
  const sameDay = date.getFullYear() === now.getFullYear()
    && date.getMonth() === now.getMonth()
    && date.getDate() === now.getDate()
  if (sameDay) return time
  const day = `${date.getMonth() + 1}月${date.getDate()}日`
  if (date.getFullYear() === now.getFullYear()) return `${day} ${time}`
  return `${date.getFullYear()}年${day} ${time}`
}

/** 用时：不足一分钟只显示秒，不足一小时不显示小时。 */
export function formatDuration(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = total % 60
  if (hours > 0) {
    return minutes > 0 ? `${hours}小时${minutes}分${seconds}秒` : `${hours}小时${seconds}秒`
  }
  if (minutes > 0) return `${minutes}分${seconds}秒`
  return `${seconds}秒`
}
