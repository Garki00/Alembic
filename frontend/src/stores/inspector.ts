import { defineStore } from 'pinia'
import type { FileDiff } from '../lib/diff'
import { useUiStore } from './ui'

export interface FilesTab {
  id: string
  kind: 'files'
  title: string
  /** 是否由用户主动创建（点加号）vs 程序打开 */
  userCreated?: boolean
}

export interface FileTab {
  id: string
  kind: 'file'
  title: string
  path: string
  /** 从变更总结打开时携带的本轮 diff；普通浏览文件时为 undefined。 */
  diff?: FileDiff
  /** 是否由用户主动创建（点加号）vs 程序打开 */
  userCreated?: boolean
}

export interface BrowserTab {
  id: string
  kind: 'browser'
  title: string
  url: string
  /** 是否由用户主动创建（点加号）vs 程序打开 */
  userCreated?: boolean
}

export type InspectorTab = FilesTab | FileTab | BrowserTab

let counter = 0
function tabId(prefix: string): string {
  counter += 1
  return `${prefix}-${Date.now().toString(36)}-${counter}`
}

const INSPECTOR_WIDTH_KEY = 'alembic.inspector-width'
const INSPECTOR_MIN = 300
const INSPECTOR_MAX = 640

function storedWidth(): number {
  try {
    const value = Number(window.localStorage.getItem(INSPECTOR_WIDTH_KEY))
    if (!Number.isFinite(value)) return 380
    return Math.min(INSPECTOR_MAX, Math.max(INSPECTOR_MIN, value))
  } catch (_) {
    return 380
  }
}

function baseName(path: string): string {
  const clean = path.replace(/\\/g, '/').replace(/\/+$/, '')
  return clean.split('/').filter(Boolean).pop() || clean || '文件'
}

export function normalizeBrowserUrl(raw: string): string {
  const text = String(raw || '').trim()
  if (!text) return ''
  if (/^https?:\/\//i.test(text)) return text
  return `https://${text}`
}

function browserTitle(url: string): string {
  if (!url) return '新标签页'
  try {
    const parsed = new URL(normalizeBrowserUrl(url))
    return parsed.hostname.replace(/^www\./, '') || '浏览器'
  } catch (_) {
    return '浏览器'
  }
}

export const useInspectorStore = defineStore('inspector', {
  state: () => ({
    open: false,
    width: storedWidth(),
    tabs: [] as InspectorTab[],
    activeId: '',
  }),

  getters: {
    activeTab(state): InspectorTab | null {
      return state.tabs.find((tab) => tab.id === state.activeId) || null
    },
    // 只计算用户主动创建的标签页（点加号创建），不计算程序打开的文件链接
    userCreatedTabCount(state): number {
      return state.tabs.filter((tab) => tab.userCreated !== false).length
    },
    canAddTab(): boolean {
      return this.userCreatedTabCount < 3
    },
  },

  actions: {
    setWidth(width: number): void {
      this.width = Math.min(INSPECTOR_MAX, Math.max(INSPECTOR_MIN, Math.round(width)))
      try {
        window.localStorage.setItem(INSPECTOR_WIDTH_KEY, String(this.width))
      } catch (_) {
        // 隐私模式下 localStorage 可能不可用；缩放仍然生效
      }
    },

    toggle(): void {
      this.open = !this.open
    },

    show(): void {
      this.open = true
    },

    hide(): void {
      this.open = false
    },

    setActive(id: string): void {
      if (this.tabs.some((tab) => tab.id === id)) this.activeId = id
    },

    addTab(tab: InspectorTab, userCreated = true): boolean {
      // 如果超过总数限制，拒绝
      if (this.tabs.length >= 10) {
        useUiStore().showToast('标签页数量已达上限', true)
        return false
      }

      // 如果是用户创建的标签页，检查用户创建数量限制
      if (userCreated && this.userCreatedTabCount >= 3) {
        useUiStore().showToast('最多打开 3 个标签页（从链接打开的文件不计入限制）', true)
        return false
      }

      // 添加 userCreated 标记
      if (typeof tab === 'object') {
        tab.userCreated = userCreated
      }

      this.tabs.push(tab)
      this.activeId = tab.id
      this.open = true
      return true
    },

    openFiles(): void {
      const existing = this.tabs.find((tab) => tab.kind === 'files')
      if (existing) {
        this.activeId = existing.id
        this.open = true
        return
      }
      this.addTab({ id: tabId('files'), kind: 'files', title: '工作区文件' }, true)
    },

    openFile(path: string, diff?: FileDiff): void {
      const clean = String(path || '').replace(/\\/g, '/').replace(/^\.?\//, '')
      if (!clean) return
      const existing = this.tabs.find((tab) => tab.kind === 'file' && tab.path === clean)
      if (existing) {
        if (existing.kind === 'file') existing.diff = diff
        this.activeId = existing.id
        this.open = true
        return
      }
      // 从链接打开的文件不计入用户创建的标签页限制
      this.addTab(
        {
          id: tabId('file'),
          kind: 'file',
          title: baseName(clean),
          path: clean,
          diff,
        },
        false, // userCreated = false
      )
    },

    openBrowser(url = ''): void {
      const target = normalizeBrowserUrl(url)
      this.addTab(
        {
          id: tabId('browser'),
          kind: 'browser',
          title: browserTitle(target),
          url: target,
        },
        true, // userCreated = true (手动打开浏览器)
      )
    },

    /** 从对话里的网页链接打开：复用同 URL 标签页，且不计入用户标签页配额。 */
    openLink(url = ''): void {
      const target = normalizeBrowserUrl(url)
      if (!target) return
      const existing = this.tabs.find(
        (tab) => tab.kind === 'browser' && tab.url === target,
      )
      if (existing) {
        this.activeId = existing.id
        this.open = true
        return
      }
      if (this.tabs.length >= 10) {
        // 标签页已经很多时不再新增，直接复用当前浏览器标签。
        const browser = this.tabs.find((tab) => tab.kind === 'browser')
        if (browser && browser.kind === 'browser') {
          browser.url = target
          browser.title = browserTitle(target)
          this.activeId = browser.id
          this.open = true
          return
        }
      }
      this.addTab(
        {
          id: tabId('browser'),
          kind: 'browser',
          title: browserTitle(target),
          url: target,
        },
        false,
      )
    },

    updateBrowser(id: string, url: string): void {
      const tab = this.tabs.find((item) => item.id === id && item.kind === 'browser')
      if (!tab || tab.kind !== 'browser') return
      const target = normalizeBrowserUrl(url)
      tab.url = target
      tab.title = browserTitle(target)
    },

    closeTab(id: string): void {
      const index = this.tabs.findIndex((tab) => tab.id === id)
      if (index < 0) return
      this.tabs.splice(index, 1)
      if (this.activeId === id) {
        const next = this.tabs[index] || this.tabs[index - 1]
        this.activeId = next?.id || ''
      }
    },
  },
})
