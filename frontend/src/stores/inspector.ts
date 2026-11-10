import { defineStore } from 'pinia'
import { useUiStore } from './ui'

export interface FilesTab {
  id: string
  kind: 'files'
  title: string
}

export interface FileTab {
  id: string
  kind: 'file'
  title: string
  path: string
}

export interface BrowserTab {
  id: string
  kind: 'browser'
  title: string
  url: string
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
    canAddTab(state): boolean {
      return state.tabs.length < 3
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

    addTab(tab: InspectorTab): boolean {
      if (this.tabs.length >= 3) {
        useUiStore().showToast('最多打开 3 个标签页', true)
        return false
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
      this.addTab({ id: tabId('files'), kind: 'files', title: '工作区文件' })
    },

    openFile(path: string): void {
      const clean = String(path || '').replace(/\\/g, '/').replace(/^\.?\//, '')
      if (!clean) return
      const existing = this.tabs.find((tab) => tab.kind === 'file' && tab.path === clean)
      if (existing) {
        this.activeId = existing.id
        this.open = true
        return
      }
      this.addTab({
        id: tabId('file'),
        kind: 'file',
        title: baseName(clean),
        path: clean,
      })
    },

    openBrowser(url = ''): void {
      const target = normalizeBrowserUrl(url)
      this.addTab({
        id: tabId('browser'),
        kind: 'browser',
        title: browserTitle(target),
        url: target,
      })
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
