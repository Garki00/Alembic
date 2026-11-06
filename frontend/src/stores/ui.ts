import { defineStore } from 'pinia'

export type SettingsTab = 'models' | 'tools'

let toastTimer: number | undefined

export const useUiStore = defineStore('ui', {
  state: () => ({
    sidebarOpen: false,
    search: '',
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
