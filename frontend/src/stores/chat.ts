import { defineStore } from 'pinia'
import { api, errorMessage } from '../api/client'
import { streamChat } from '../api/chat'
import type {
  ApprovalDecision,
  ApprovalRequest,
  ChatDoneEvent,
  HistoryData,
  HistoryMessage,
} from '../api/types'
import {
  createAssistantTurn,
  newId,
  type AssistantTurn,
  type ChatApproval,
  type ChatTurn,
} from '../types/chat'
import { useUiStore } from './ui'
import { useWorkspaceStore } from './workspace'

// 当前正在写入的消息上下文。用户切换会话时递增，旧流的事件就不会污染新会话。
let activeStream = 0
let abortController: AbortController | null = null

function mapHistory(items: HistoryMessage[]): ChatTurn[] {
  const turns: ChatTurn[] = []
  let assistant: AssistantTurn | null = null

  for (const message of items) {
    if (message.role === 'user') {
      assistant = null
      turns.push({ kind: 'user', id: newId('user'), content: message.content })
      continue
    }
    if (message.role === 'assistant') {
      if (!assistant) {
        assistant = createAssistantTurn()
        turns.push(assistant)
      }
      for (const call of message.tool_calls || []) {
        assistant.toolCalls.push({ name: call.name, args: call.args || {} })
      }
      if (message.content) assistant.content = message.content
      continue
    }
    if (message.role === 'tool') {
      if (!assistant) {
        assistant = createAssistantTurn()
        turns.push(assistant)
      }
      assistant.toolResults.push({
        name: message.tool_name || '工具',
        content: message.content,
      })
    }
    // system / 摘要消息不进入对话流（与原前端一致）
  }
  return turns
}

export const useChatStore = defineStore('chat', {
  state: () => ({
    sessionId: null as string | null,
    messages: [] as ChatTurn[],
    streaming: false,
    contextMeter: '',
    streamTick: 0,
  }),

  actions: {
    bump(): void {
      this.streamTick += 1
    },

    resetConversation(): void {
      this.messages = []
      this.contextMeter = ''
      this.bump()
    },

    stop(): void {
      abortController?.abort()
    },

    async openSession(sessionId: string): Promise<void> {
      this.stop()
      activeStream += 1
      this.streaming = false
      this.sessionId = sessionId
      try {
        const data = await api<HistoryData>(
          `/api/sessions/${encodeURIComponent(sessionId)}/messages`,
        )
        this.messages = mapHistory(data.messages || [])
        this.contextMeter = data.turns
          ? `${data.turns} 轮 · ${(data.messages || []).length} 条消息`
          : ''
      } catch (error) {
        this.messages = []
        this.contextMeter = ''
        this.bump()
        throw error
      }
      this.bump()
    },

    async newSession(workspaceId: string): Promise<void> {
      this.stop()
      activeStream += 1
      this.streaming = false
      const workspace = useWorkspaceStore()
      const session = await workspace.createSession(workspaceId)
      this.sessionId = session.id
      this.messages = []
      this.contextMeter = ''
      this.bump()
    },

    async send(message: string, workspaceId?: string): Promise<void> {
      const text = message.trim()
      if (!text || this.streaming) return

      const ui = useUiStore()
      const workspace = useWorkspaceStore()
      // 先把消息推进响应式数组，再从数组里取回代理对象；
      // 直接改 createAssistantTurn() 返回的原始对象不会触发 Vue 更新。
      this.messages.push(
        { kind: 'user', id: newId('user'), content: text },
        createAssistantTurn(),
      )
      const assistant = this.messages[this.messages.length - 1] as AssistantTurn
      assistant.streaming = true
      this.streaming = true
      this.bump()

      const streamId = ++activeStream
      const isCurrent = () => streamId === activeStream

      let finalText = ''
      let frame = 0
      const flush = () => {
        if (frame) {
          window.cancelAnimationFrame(frame)
          frame = 0
        }
        if (assistant.content !== finalText) assistant.content = finalText
      }
      const schedule = () => {
        if (frame) return
        frame = window.requestAnimationFrame(() => {
          frame = 0
          assistant.content = finalText
          this.bump()
        })
      }

      const controller = new AbortController()
      abortController = controller

      try {
        await streamChat(
          {
            session_id: this.sessionId,
            message: text,
            workspace_id: workspaceId,
          },
          (event, data) => {
            if (!isCurrent()) return
            switch (event) {
              case 'meta': {
                const sid = String(data.session_id || '')
                if (!this.sessionId && sid) this.sessionId = sid
                break
              }
              case 'delta': {
                finalText += String(data.text ?? '')
                schedule()
                break
              }
              case 'tool': {
                assistant.toolCalls.push({
                  name: String(data.name || 'tool'),
                  args: (data.args as Record<string, unknown>) || {},
                })
                this.bump()
                break
              }
              case 'approval': {
                const request = data as unknown as ApprovalRequest
                assistant.approvals.push({
                  ...request,
                  status: 'pending',
                  submitting: false,
                })
                this.bump()
                break
              }
              case 'approval_expired': {
                const id = String(data.id || '')
                const approval = assistant.approvals.find((item) => item.id === id)
                if (approval) {
                  approval.status = 'expired'
                  this.bump()
                }
                break
              }
              case 'compacted': {
                this.messages.push({
                  kind: 'note',
                  id: newId('note'),
                  text: '上下文已自动压缩，较早的对话折叠为摘要',
                })
                this.bump()
                break
              }
              case 'done': {
                const done = data as unknown as ChatDoneEvent
                if (done.text !== undefined) finalText = done.text
                if (done.session_id) this.sessionId = done.session_id
                const tokens = done.tokens ?? '?'
                const source = done.token_source === 'provider' ? 'API 用量' : '本地估算'
                this.contextMeter = `约 ${tokens} tokens · ${source} · ${done.turns ?? 0} 轮`
                void workspace.refreshSessions().catch(() => { /* 列表刷新失败不影响对话 */ })
                break
              }
              case 'error': {
                throw new Error(String(data.message || '生成失败'))
              }
            }
          },
          controller.signal,
        )
      } catch (error) {
        if (!isCurrent()) return
        if (controller.signal.aborted) {
          this.messages.push({ kind: 'note', id: newId('note'), text: '已停止生成' })
        } else {
          const message = errorMessage(error)
          assistant.error = `出错了：${message}`
          ui.showToast(message, true)
        }
      } finally {
        if (isCurrent()) {
          flush()
          assistant.streaming = false
          this.streaming = false
          abortController = null
          this.bump()
        }
      }
    },

    async decideApproval(
      approval: ChatApproval,
      decision: ApprovalDecision,
    ): Promise<void> {
      if (approval.status !== 'pending' || approval.submitting) return
      const ui = useUiStore()
      approval.submitting = true
      try {
        await api(`/api/approvals/${encodeURIComponent(approval.id)}`, {
          method: 'POST',
          body: { decision },
        })
        approval.status = decision === 'deny' ? 'denied' : decision
        if (decision === 'always') ui.showToast('本会话内将自动允许同类操作')
      } catch (error) {
        ui.showToast(errorMessage(error), true)
      } finally {
        approval.submitting = false
      }
    },

    async clearApprovals(): Promise<number> {
      const ui = useUiStore()
      const query = encodeURIComponent(this.sessionId || 'default')
      const data = await api<{ cleared: number }>(`/api/approvals?session_id=${query}`, {
        method: 'DELETE',
      })
      const cleared = data.cleared || 0
      ui.showToast(cleared ? `已清除 ${cleared} 条授权` : '本会话没有可清除的授权')
      return cleared
    },
  },
})
