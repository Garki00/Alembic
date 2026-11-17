import { defineStore } from 'pinia'

export type SettingsTab = 'general' | 'models' | 'tools'

export const SIDEBAR_WIDTH_KEY = 'alembic.sidebar-width'
export const SIDEBAR_MIN = 220
export const SIDEBAR_MAX = 420

function storedWidth(key: string, fallback: number, min: number, max: number): number {
  try {
    const value = Number(window.localStorage.getItem(key))
    if (!Number.isFinite(value)) return fallback
    return Math.min(max, Math.max(min, value))
  } catch (_) {
    return fallback
  }
}

let toastTimer: number | undefined

export const useUiStore = defineStore('ui', {
  state: () => ({
    sidebarOpen: false,
    sidebarCollapsed: false,
    sidebarWidth: storedWidth(SIDEBAR_WIDTH_KEY, 272, SIDEBAR_MIN, SIDEBAR_MAX),
    search: '',
    searchOpen: false,
    sessionGrouping: 'workspace' as 'workspace' | 'flat',
    sessionSort: 'recent' as 'recent' | 'manual',
    manualOrder: [] as string[],
    archivedOpen: false,
    expanded: {} as Record<string, boolean>,
    settingsOpen: false,
    settingsTab: 'models' as SettingsTab,
    workspaceModalOpen: false,
    toastVisible: false,
    toastMessage: '',
    toastError: false,
    // 欢迎页的推荐问题通过它把文本塞进输入框
    composerDraft: '',
    composerFocusTick: 0,
  }),

  actions: {
    setSidebarWidth(width: number): void {
      this.sidebarWidth = Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, Math.round(width)))
      try {
        window.localStorage.setItem(SIDEBAR_WIDTH_KEY, String(this.sidebarWidth))
      } catch (_) {
        // 隐私模式下 localStorage 可能不可用；缩放仍然生效
      }
    },

    toggleSidebarCollapsed(): void {
      this.sidebarCollapsed = !this.sidebarCollapsed
    },

    setSessionGrouping(grouping: 'workspace' | 'flat'): void {
      this.sessionGrouping = grouping
    },

    setSessionSort(sort: 'recent' | 'manual'): void {
      this.sessionSort = sort
      if (sort === 'recent') this.manualOrder = []
    },

    setManualOrder(ids: string[]): void {
      this.manualOrder = [...new Set(ids)]
      this.sessionSort = 'manual'
    },

    isExpanded(workspaceId: string): boolean {
      return this.expanded[workspaceId] !== false
    },

    toggleExpanded(workspaceId: string): void {
      this.expanded[workspaceId] = !this.isExpanded(workspaceId)
    },

    openSettings(tab: SettingsTab = 'models'): void {
      this.settingsTab = tab
      this.settingsOpen = true
      this.sidebarOpen = false
    },

    closeSettings(): void {
      this.settingsOpen = false
    },

    openWorkspaceModal(): void {
      this.workspaceModalOpen = true
      this.sidebarOpen = false
    },

    closeWorkspaceModal(): void {
      this.workspaceModalOpen = false
    },

    showToast(message: string, isError = false): void {
      this.toastMessage = message
      this.toastError = isError
      this.toastVisible = true
      if (toastTimer !== undefined) window.clearTimeout(toastTimer)
      toastTimer = window.setTimeout(() => {
        this.toastVisible = false
        toastTimer = undefined
      }, 2800)
    },

    setComposerDraft(text: string): void {
      this.composerDraft = text
      this.composerFocusTick += 1
    },

    closeOverlays(): void {
      this.settingsOpen = false
      this.workspaceModalOpen = false
      this.sidebarOpen = false
    },
  },
})
