import { defineStore } from 'pinia'
import { api, errorMessage } from '../api/client'
import { streamChat } from '../api/chat'
import type {
  ApprovalDecision,
  ApprovalRequest,
  ChatDoneEvent,
  HistoryData,
  HistoryMessage,
  QuestionRequest,
  TodoItem,
  TodosData,
} from '../api/types'
import {
  createAssistantTurn,
  newId,
  type AssistantTurn,
  type ChatApproval,
  type ChatQuestion,
  type ChatTurn,
  type WorkEvent,
} from '../types/chat'
import { useUiStore } from './ui'
import { useWorkspaceStore } from './workspace'

// 当前正在写入的消息上下文。用户切换会话时递增，旧流的事件就不会污染新会话。
let activeStream = 0
let abortController: AbortController | null = null

type ToolWorkEvent = Extract<WorkEvent, { kind: 'tool' }>

function findOpenToolEvent(
  events: WorkEvent[],
  id: string | undefined,
  name: string,
): ToolWorkEvent | undefined {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index]
    if (event.kind !== 'tool' || event.result) continue
    if (id ? event.id === id : event.name === name) return event
  }
  if (id) {
    // Older histories may not carry a call id; fall back to the tool name.
    for (let index = events.length - 1; index >= 0; index -= 1) {
      const event = events[index]
      if (event.kind === 'tool' && !event.result && event.name === name) return event
    }
  }
  return undefined
}

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
      if (message.reasoning?.trim()) {
        assistant.workEvents.push({
          kind: 'thought',
          id: newId('thought'),
          text: message.reasoning.trim(),
        })
      }
      if (message.content) {
        if (message.tool_calls?.length) {
          assistant.workEvents.push({
            kind: 'thought',
            id: newId('thought'),
            text: message.content,
          })
        } else {
          assistant.content = message.content
        }
      }
      for (const call of message.tool_calls || []) {
        const eventId = call.id || newId('tool')
        assistant.toolCalls.push({ id: call.id, name: call.name, args: call.args || {} })
        assistant.workEvents.push({
          kind: 'tool',
          id: eventId,
          name: call.name,
          args: call.args || {},
          result: '',
          error: false,
        })
      }
      continue
    }
    if (message.role === 'tool') {
      if (!assistant) {
        assistant = createAssistantTurn()
        turns.push(assistant)
      }
      const content = message.content
      assistant.toolResults.push({
        id: message.tool_call_id,
        name: message.tool_name || '工具',
        content,
      })
      const event = findOpenToolEvent(
        assistant.workEvents,
        message.tool_call_id,
        message.tool_name || '',
      )
      if (event) {
        event.result = content
        event.error = content.startsWith('Error:')
      } else {
        assistant.workEvents.push({
          kind: 'tool',
          id: message.tool_call_id || newId('tool'),
          name: message.tool_name || '工具',
          args: {},
          result: content,
          error: content.startsWith('Error:'),
        })
      }
    }
    // system / 摘要消息不进入对话流（与原前端一致）
  }
  for (const turn of turns) {
    if (turn.kind === 'assistant') {
      // 历史轮次默认收起，由用户点开；当前活动轮次在发送时单独控制。
      turn.stepsOpen = false
    }
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
    todos: [] as TodoItem[],
  }),

  getters: {
    pendingApproval(state): ChatApproval | null {
      for (let index = state.messages.length - 1; index >= 0; index -= 1) {
        const turn = state.messages[index]
        if (turn.kind !== 'assistant') continue
        const found = turn.approvals.find((item) => item.status === 'pending')
        if (found) return found
      }
      return null
    },

    pendingQuestion(state): ChatQuestion | null {
      for (let index = state.messages.length - 1; index >= 0; index -= 1) {
        const turn = state.messages[index]
        if (turn.kind !== 'assistant') continue
        const found = turn.questions.find((item) => item.status === 'pending')
        if (found) return found
      }
      return null
    },
  },

  actions: {
    bump(): void {
      this.streamTick += 1
    },

    resetConversation(): void {
      this.messages = []
      this.contextMeter = ''
      this.todos = []
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
        try {
          const todos = await api<TodosData>(
            `/api/sessions/${encodeURIComponent(sessionId)}/todos`,
          )
          this.todos = todos.items || []
        } catch (_) {
          this.todos = []
        }
      } catch (error) {
        this.messages = []
        this.contextMeter = ''
        this.todos = []
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
      this.todos = []
      this.bump()
    },

    async send(message: string, workspaceId?: string): Promise<void> {
      const text = message.trim()
      if (!text || this.streaming) return

      const ui = useUiStore()
      const workspace = useWorkspaceStore()
      const prefs = workspace.preferences
      if (prefs.transcript_view !== 'verbose' && prefs.collapse_timing === 'next_input') {
        for (const turn of this.messages) {
          if (turn.kind === 'assistant' && !turn.stepsManual) turn.stepsOpen = false
        }
      }

      // 在工作区里直接发送时，先真正创建一个会话，避免把 null 传给后端。
      if (!this.sessionId && workspaceId) {
        try {
          const session = await workspace.createSession(workspaceId)
          this.sessionId = session.id
        } catch (error) {
          ui.showToast(errorMessage(error), true)
          return
        }
      }

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
            session_id: this.sessionId || undefined,
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
              case 'thought': {
                const text = String(data.text || '')
                if (!text) break
                const id = data.id ? String(data.id) : newId('thought')
                const existing = data.append
                  ? assistant.workEvents.find(
                      (event) => event.kind === 'thought' && event.id === id,
                    )
                  : undefined
                if (existing?.kind === 'thought') {
                  existing.text += text
                } else if (text.trim()) {
                  assistant.workEvents.push({ kind: 'thought', id, text })
                } else {
                  break
                }
                this.bump()
                break
              }
              case 'tool': {
                const id = data.id ? String(data.id) : newId('tool')
                assistant.toolCalls.push({
                  id: data.id ? String(data.id) : undefined,
                  name: String(data.name || 'tool'),
                  args: (data.args as Record<string, unknown>) || {},
                })
                assistant.workEvents.push({
                  kind: 'tool',
                  id,
                  name: String(data.name || 'tool'),
                  args: (data.args as Record<string, unknown>) || {},
                  result: '',
                  error: false,
                })
                this.bump()
                break
              }
              case 'tool_result': {
                const id = data.id ? String(data.id) : undefined
                const name = String(data.name || '工具')
                const content = String(data.content ?? '')
                assistant.toolResults.push({ id, name, content })
                const event = findOpenToolEvent(assistant.workEvents, id, name)
                if (event) {
                  event.result = content
                  event.error = content.startsWith('Error:')
                } else {
                  assistant.workEvents.push({
                    kind: 'tool',
                    id: id || newId('tool'),
                    name,
                    args: {},
                    result: content,
                    error: content.startsWith('Error:'),
                  })
                }
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
              case 'question': {
                const request = data as unknown as QuestionRequest
                assistant.questions.push({
                  ...request,
                  options: request.options || [],
                  status: 'pending',
                  answer: '',
                  submitting: false,
                })
                this.bump()
                break
              }
              case 'question_expired': {
                const id = String(data.id || '')
                const question = assistant.questions.find((item) => item.id === id)
                if (question) {
                  question.status = 'expired'
                  this.bump()
                }
                break
              }
              case 'todo': {
                this.todos = (data.items as TodoItem[]) || []
                this.bump()
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
                const livePrefs = workspace.preferences
                if (
                  livePrefs.transcript_view !== 'verbose'
                  && livePrefs.collapse_timing === 'completion'
                  && !assistant.stepsManual
                ) {
                  assistant.stepsOpen = false
                }
                if (livePrefs.transcript_view === 'verbose') assistant.stepsOpen = true
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
          for (const question of assistant.questions) {
            if (question.status === 'pending') question.status = 'expired'
          }
          this.messages.push({ kind: 'note', id: newId('note'), text: '已停止生成' })
        } else {
          const message = errorMessage(error)
          assistant.error = `出错了：${message}`
          ui.showToast(message, true)
        }
      } finally {
        if (isCurrent()) {
          const finalPrefs = workspace.preferences
          if (
            finalPrefs.transcript_view !== 'verbose'
            && finalPrefs.collapse_timing === 'completion'
            && !assistant.stepsManual
          ) {
            assistant.stepsOpen = false
          }
          flush()
          assistant.streaming = false
          this.streaming = false
          abortController = null
          this.bump()
        }
      }
    },

    toggleSteps(turn: AssistantTurn): void {
      const workspace = useWorkspaceStore()
      if (workspace.preferences.transcript_view === 'verbose') return
      turn.stepsOpen = !turn.stepsOpen
      turn.stepsManual = true
      this.bump()
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

    async answerQuestion(question: ChatQuestion, answer: string): Promise<void> {
      if (question.status !== 'pending' || question.submitting) return
      const ui = useUiStore()
      question.submitting = true
      try {
        await api(`/api/questions/${encodeURIComponent(question.id)}`, {
          method: 'POST',
          body: { answer },
        })
        question.answer = answer
        question.status = 'answered'
      } catch (error) {
        ui.showToast(errorMessage(error), true)
      } finally {
        question.submitting = false
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
