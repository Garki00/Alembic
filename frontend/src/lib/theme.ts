// 外观：浅色 / 深色 / 跟随系统。
//
// 主题只写在 <html data-theme> 上，具体配色由 main.css 的变量决定；
// 服务端只保存用户的选择（light / dark / system）。

import type { ThemeMode } from '../api/types'

const query = window.matchMedia('(prefers-color-scheme: dark)')

export function resolvedTheme(mode: ThemeMode | undefined): 'light' | 'dark' {
  if (mode === 'dark') return 'dark'
  if (mode === 'system') return query.matches ? 'dark' : 'light'
  return 'light'
}

/** 把当前选择应用到文档上；返回实际生效的主题。 */
export function applyTheme(mode: ThemeMode | undefined): 'light' | 'dark' {
  const resolved = resolvedTheme(mode)
  const root = document.documentElement
  root.dataset.theme = resolved
  root.style.colorScheme = resolved
  return resolved
}

/** 订阅系统外观变化，返回取消订阅函数。 */
export function watchSystemTheme(handler: () => void): () => void {
  const listener = (): void => handler()
  query.addEventListener('change', listener)
  return () => query.removeEventListener('change', listener)
}
