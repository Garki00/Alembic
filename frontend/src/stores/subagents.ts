import { defineStore } from 'pinia'
import { api, errorMessage } from '../api/client'
import { streamSubagentEvents } from '../api/chat'
import type {
  ApprovalDecision,
  ApprovalRequest,
  SubagentDetailData,
  SubagentInfo,
  SubagentListData,
} from '../api/types'
import type { ChatApproval } from '../types/chat'
import { useChatStore } from './chat'
import { useInspectorStore } from './inspector'
import { useUiStore } from './ui'

let eventsAbort: AbortController | null = null

export interface SubagentLiveStep {
  phase: string
  id?: string
  name?: string
  text?: string
  content?: string
  args?: Record<string, unknown>
  error?: boolean
  append?: boolean
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

/**
 * Subagent channel: the session task list, background approvals, and completion
 * notifications that outlive one chat turn.
 */
export const useSubagentStore = defineStore('subagents', {
  state: () => ({
    items: [] as SubagentInfo[],
    panelOpen: false,
    runningOpen: true,
    finishedOpen: true,
    expandedId: '',
    approvals: [] as ChatApproval[],
    /** Live work-step events per subagent, for the sidebar conversation. */
    liveSteps: {} as Record<string, SubagentLiveStep[]>,
    /** Live assistant text per subagent. */
    liveText: {} as Record<string, string>,
    /** Prompt of the run currently streaming, for the live conversation view. */
    livePrompt: {} as Record<string, string>,
    /** Bumped on every live event so views can watch a scalar instead of deep arrays. */
    liveTick: 0,
  }),

  getters: {
    running(state): SubagentInfo[] {
      return state.items.filter((item) => item.status === 'running')
    },
    finished(state): SubagentInfo[] {
      return state.items.filter((item) => item.status !== 'running')
    },
    runningCount(): number {
      return this.running.length
    },
    pendingCount(state): number {
      return state.approvals.filter((item) => item.status === 'pending').length
    },
  },

  actions: {
    async refresh(): Promise<void> {
      const chat = useChatStore()
      if (!chat.sessionId) {
        this.items = []
        return
      }
      try {
        const data = await api<SubagentListData>(
          `/api/subagents?session_id=${encodeURIComponent(chat.sessionId)}`,
        )
        this.items = data.subagents || []
        if (this.expandedId && !this.items.some((item) => item.id === this.expandedId)) {
          this.expandedId = ''
        }
      } catch (_) {
        // The panel is auxiliary; the next event or poll retries.
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
            await streamSubagentEvents((event, data) => {
              if (event === 'praise') {
                const lines = Array.isArray(data.lines)
                  ? data.lines.map((line) => String(line)).filter(Boolean)
                  : []
                const sessionId = String(data.session_id || '')
                if (sessionId) chat.appendPraise(sessionId, lines)
                return
              }
              if (event === 'approval') {
                const request = data as unknown as ApprovalRequest & { agent_id?: string }
                if (!this.approvals.some((item) => item.id === request.id)) {
                  this.approvals.push({
                    ...request,
                    status: 'pending',
                    submitting: false,
                  })
                }
                return
              }
              if (event === 'subagent_started') {
                const agentId = String(data.agent_id || '')
                if (agentId) {
                  this.liveSteps[agentId] = []
                  this.liveText[agentId] = ''
                  this.livePrompt[agentId] = String(data.prompt || '')
                  this.liveTick += 1
                }
                void this.refresh()
                return
              }
              if (event === 'subagent_progress') {
                this.appendLiveStep(data)
                return
              }
              if (event === 'subagent_backgrounded') {
                void this.refresh()
                return
              }
              if (event !== 'subagent_completed') return
              void this.refresh()
              const record = data as unknown as {
                agent_id: string
                session_id: string
                description?: string
                status?: string
                background?: boolean
                error?: string
              }
              if (!record.background) return
              if (record.status === 'failed' && record.error) {
                ui.showToast(`子代理失败：${record.error}`, true)
              }
              if (record.session_id && record.agent_id) {
                chat.queueSubagentContinuation({
                  id: record.agent_id,
                  session_id: record.session_id,
                  description: record.description,
                  status: record.status,
                })
              }
            }, controller.signal)
          } catch (error) {
            if (controller.signal.aborted) return
            if (this.pendingCount) ui.showToast(errorMessage(error), true)
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

    appendLiveStep(data: Record<string, unknown>): void {
      const agentId = String(data.agent_id || '')
      if (!agentId) return
      const phase = String(data.phase || '')
      if (phase === 'text') {
        const text = String(data.text || '')
        if (!text) return
        this.liveText[agentId] = `${this.liveText[agentId] || ''}${text}`.slice(-200_000)
        this.liveTick += 1
        return
      }
      const steps = this.liveSteps[agentId] || (this.liveSteps[agentId] = [])
      steps.push({
        phase,
        id: data.id ? String(data.id) : undefined,
        name: data.name ? String(data.name) : undefined,
        text: data.text ? String(data.text) : undefined,
        content: data.content ? String(data.content) : undefined,
        args: (data.args as Record<string, unknown>) || undefined,
        error: Boolean(data.error),
        append: typeof data.append === 'boolean' ? data.append : undefined,
      })
      if (steps.length > 400) steps.splice(0, steps.length - 400)
      this.liveTick += 1
    },

    togglePanel(): void {
      this.panelOpen = !this.panelOpen
      if (this.panelOpen) {
        void this.refresh()
        this.runningOpen = true
      } else {
        this.expandedId = ''
      }
    },

    closePanel(): void {
      this.panelOpen = false
      this.expandedId = ''
    },

    toggleTask(id: string): void {
      this.expandedId = this.expandedId === id ? '' : id
    },

    openInSidebar(id: string): void {
      const item = this.items.find((entry) => entry.id === id)
      if (!item) return
      useInspectorStore().openSubagent(
        item.id,
        item.description || item.agent_type || '子代理',
      )
    },

    async loadDetail(id: string): Promise<SubagentDetailData | null> {
      try {
        return await api<SubagentDetailData>(`/api/subagents/${encodeURIComponent(id)}`)
      } catch (error) {
        useUiStore().showToast(errorMessage(error), true)
        return null
      }
    },

    async stopTask(id: string): Promise<void> {
      const ui = useUiStore()
      try {
        await api(`/api/subagents/${encodeURIComponent(id)}/stop`, { method: 'POST' })
        ui.showToast('已请求停止子代理')
        await this.refresh()
      } catch (error) {
        ui.showToast(errorMessage(error), true)
      }
    },

    async decide(approval: ChatApproval, decision: ApprovalDecision): Promise<void> {
      if (approval.status !== 'pending' || approval.submitting) return
      const ui = useUiStore()
      approval.submitting = true
      try {
        await api(`/api/approvals/${encodeURIComponent(approval.id)}`, {
          method: 'POST',
          body: { decision },
        })
        approval.status = decision === 'deny' ? 'denied' : decision
      } catch (error) {
        ui.showToast(errorMessage(error), true)
      } finally {
        approval.submitting = false
        this.approvals = this.approvals.filter((item) => item.status === 'pending')
      }
    },

    dismissExpired(id: string): void {
      this.approvals = this.approvals.filter((item) => item.id !== id)
    },
  },
})
