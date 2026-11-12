import { defineStore } from 'pinia'
import { api, errorMessage } from '../api/client'
import { streamProcessEvents } from '../api/chat'
import type { ProcessDetailData, ProcessInfo, ProcessListData } from '../api/types'
import { useChatStore } from './chat'
import { useUiStore } from './ui'

let eventsAbort: AbortController | null = null

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

export const useProcessStore = defineStore('processes', {
  state: () => ({
    items: [] as ProcessInfo[],
    loading: false,
    panelOpen: false,
    runningOpen: true,
    finishedOpen: false,
    expandedId: '',
    detailLog: '',
    detailLoading: false,
  }),

  getters: {
    running(state): ProcessInfo[] {
      return state.items.filter((item) => item.running)
    },
    finished(state): ProcessInfo[] {
      return state.items.filter((item) => !item.running)
    },
    runningCount(): number {
      return this.running.length
    },
  },

  actions: {
    async refresh(): Promise<void> {
      this.loading = true
      try {
        const data = await api<ProcessListData>('/api/processes')
        this.items = data.processes || []
        if (this.expandedId && !this.items.some((item) => item.id === this.expandedId)) {
          this.expandedId = ''
          this.detailLog = ''
        }
      } catch (_) {
        // 进程列表失败不打断对话；下一次事件/刷新会重试
      } finally {
        this.loading = false
      }
    },

    init(): void {
      void this.refresh()
      this.connectEvents()
    },

    connectEvents(): void {
      if (eventsAbort) return
      const controller = new AbortController()
      eventsAbort = controller
      const chat = useChatStore()
      const ui = useUiStore()

      const loop = async (): Promise<void> => {
        while (!controller.signal.aborted) {
          try {
            await streamProcessEvents((event, data) => {
              if (event !== 'process_started' && event !== 'process_completed') return
              const record = data as unknown as ProcessInfo
              void this.refresh()
              if (event === 'process_completed') {
                if (this.expandedId === record.id) void this.loadDetail(record.id)
                if (record.notify && record.session_id && record.session_id === chat.sessionId) {
                  chat.queueProcessContinuation(record)
                }
              }
            }, controller.signal)
          } catch (error) {
            if (controller.signal.aborted) return
            if (this.panelOpen) ui.showToast(errorMessage(error), true)
          }
          if (controller.signal.aborted) return
          await delay(1500)
        }
      }
      void loop()
    },

    disconnectEvents(): void {
      eventsAbort?.abort()
      eventsAbort = null
    },

    togglePanel(): void {
      this.panelOpen = !this.panelOpen
      if (this.panelOpen) {
        void this.refresh()
        this.runningOpen = true
      } else {
        this.expandedId = ''
        this.detailLog = ''
      }
    },

    closePanel(): void {
      this.panelOpen = false
      this.expandedId = ''
      this.detailLog = ''
    },

    toggleTask(id: string): void {
      if (this.expandedId === id) {
        this.expandedId = ''
        this.detailLog = ''
        return
      }
      this.expandedId = id
      void this.loadDetail(id)
    },

    async loadDetail(id: string): Promise<void> {
      this.detailLoading = true
      try {
        const data = await api<ProcessDetailData>(`/api/processes/${encodeURIComponent(id)}`)
        if (this.expandedId === id) {
          this.detailLog = data.log || ''
          const index = this.items.findIndex((item) => item.id === id)
          if (index >= 0) this.items[index] = data.process
        }
      } catch (error) {
        if (this.expandedId === id) this.detailLog = `读取输出失败：${errorMessage(error)}`
      } finally {
        this.detailLoading = false
      }
    },

    async stopTask(id: string): Promise<void> {
      const ui = useUiStore()
      try {
        await api(`/api/processes/${encodeURIComponent(id)}/stop`, { method: 'POST' })
        ui.showToast('后台任务已停止')
        await this.refresh()
        if (this.expandedId === id) await this.loadDetail(id)
      } catch (error) {
        ui.showToast(errorMessage(error), true)
      }
    },
  },
})
