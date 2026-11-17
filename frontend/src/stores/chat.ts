import { defineStore } from 'pinia'
import { api, apiUrl, authHeaders, errorMessage } from '../api/client'
import { streamChat } from '../api/chat'
import type {
  ApprovalDecision,
  ApprovalRequest,
  ChatDoneEvent,
  CompactData,
  HistoryData,
  HistoryMessage,
  ProcessInfo,
  QuestionRequest,
  TodoItem,
  TodosData,
  UsageInfo,
} from '../api/types'
import {
  createAssistantTurn,
  newId,
  type AssistantTurn,
  type ChatApproval,
  type ChatQuestion,
  type ChatTurn,
  type UserAttachment,
  type WorkEvent,
} from '../types/chat'
import { useUiStore } from './ui'
import { useWorkspaceStore } from './workspace'

interface SendOptions {
  attachments?: UserAttachment[]
  internal?: boolean
  processId?: string
}

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

function thoughtKey(text: string): string {
  // Providers sometimes normalize spaces/newlines between the streamed
  // reasoning chunks and the final message. Compare without whitespace.
  return text.replace(/\s+/g, '')
}

function addThought(assistant: AssistantTurn, text: string): void {
  const clean = text.trim()
  if (!clean) return
  const key = thoughtKey(clean)
  const duplicate = assistant.workEvents.some(
    (event) => event.kind === 'thought' && thoughtKey(event.text) === key,
  )
  if (duplicate) return
  assistant.workEvents.push({ kind: 'thought', id: newId('thought'), text: clean })
}

function mapHistory(items: HistoryMessage[]): ChatTurn[] {
  const turns: ChatTurn[] = []
  let assistant: AssistantTurn | null = null

  const splitAttachmentBlock = (content: string): {
    text: string
    attachments: UserAttachment[]
  } => {
    const match = /^\[Attached files\]\n([\s\S]*?)\n\[\/Attached files\]\n\n/.exec(content)
    if (!match) return { text: content, attachments: [] }
    const attachments: UserAttachment[] = []
    for (const line of match[1].split('\n')) {
      const item = /^- (.+?) \(([^,]+), (\d+) bytes\)$/.exec(line.trim())
      if (!item) continue
      const path = item[1]
      attachments.push({
        name: path.split('/').pop() || path,
        path,
        size: Number(item[3]),
        kind: item[2],
      })
    }
    return { text: content.slice(match[0].length), attachments }
  }

  for (const message of items) {
    if (message.role === 'user') {
      if (message.internal) continue
      assistant = null
      const parsed = splitAttachmentBlock(message.content)
      turns.push({
        kind: 'user',
        id: newId('user'),
        content: parsed.text,
        attachments: parsed.attachments,
      })
      continue
    }
    if (message.role === 'assistant') {
      if (!assistant) {
        assistant = createAssistantTurn()
        turns.push(assistant)
      }
      if (message.reasoning?.trim()) {
        addThought(assistant, message.reasoning)
      }
      if (message.content) {
        if (message.tool_calls?.length) {
          addThought(assistant, message.content)
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
    contextTokens: 0,
    contextMax: 0,
    // 会话累计轮次与步数（步 = 模型请求次数，含工具循环的每一步）
    turns: 0,
    steps: 0,
    // 缓存读取命中：会话累计 + 本轮（提供方上报，未上报时为 null）
    cacheReadTokens: 0,
    cachePromptTokens: 0,
    cacheHitRate: null as number | null,
    cacheRequests: 0,
    turnCacheReadTokens: 0,
    turnPromptTokens: 0,
    turnHitRate: null as number | null,
    turnAttempts: 0,
    streamTick: 0,
    todos: [] as TodoItem[],
    pendingContinuations: [] as ProcessInfo[],
  }),

  getters: {
    /** 会话是否已经有可展示的缓存数据 */
    cacheKnown(state): boolean {
      return state.cacheHitRate !== null && state.cachePromptTokens > 0
    },
    cachePercent(state): number {
      return state.cacheHitRate === null ? 0 : Math.round(state.cacheHitRate * 1000) / 10
    },
    turnCachePercent(state): number | null {
      return state.turnHitRate === null ? null : Math.round(state.turnHitRate * 1000) / 10
    },
    contextPercent(state): number {
      if (!state.contextMax || state.contextMax <= 0) return 0
      return Math.max(0, Math.min(100, Math.round((state.contextTokens / state.contextMax) * 100)))
    },
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
      this.turns = 0
      this.steps = 0
      this.contextTokens = 0
      this.contextMax = 0
      this.resetUsage()
      this.todos = []
      this.pendingContinuations = []
      this.bump()
    },

    setContext(tokens?: number, maxTokens?: number): void {
      if (typeof tokens === 'number' && Number.isFinite(tokens)) this.contextTokens = tokens
      if (typeof maxTokens === 'number' && Number.isFinite(maxTokens)) this.contextMax = maxTokens
    },

    /** 会话累计用量（含提供方上报的缓存读/写桶）。 */
    setUsage(usage?: UsageInfo): void {
      if (!usage) return
      this.cacheReadTokens = usage.cache_read_tokens ?? 0
      this.cachePromptTokens = usage.prompt_tokens ?? 0
      this.cacheHitRate = usage.hit_rate ?? null
      this.cacheRequests = usage.requests ?? usage.attempts ?? 0
      // 一次模型请求就是一步；没有步数数据时保留旧值，避免显示成 0 步
      if (typeof usage.requests === 'number') this.steps = usage.requests
    },

    /** 本轮用量：用于悬停提示与「本轮命中」。 */
    setTurnUsage(usage?: UsageInfo): void {
      if (!usage) return
      this.turnCacheReadTokens = usage.cache_read_tokens ?? 0
      this.turnPromptTokens = usage.prompt_tokens ?? 0
      this.turnHitRate = usage.hit_rate ?? null
      this.turnAttempts = usage.attempts ?? 0
    },

    resetUsage(): void {
      this.cacheReadTokens = 0
      this.cachePromptTokens = 0
      this.cacheHitRate = null
      this.cacheRequests = 0
      this.turnCacheReadTokens = 0
      this.turnPromptTokens = 0
      this.turnHitRate = null
      this.turnAttempts = 0
    },

    stop(): void {
      const sessionId = this.sessionId
      if (sessionId) {
        void fetch(apiUrl(`/api/sessions/${encodeURIComponent(sessionId)}/cancel?background=1`), {
          method: 'POST',
          headers: authHeaders(),
          credentials: 'include',
          keepalive: true,
        }).catch(() => { /* 断流时的兜底，服务端也会在 finally 里取消 */ })
      }
      this.pendingContinuations = []
      abortController?.abort()
    },

    stopStreamOnly(): void {
      const sessionId = this.sessionId
      if (this.streaming && sessionId) {
        void fetch(apiUrl(`/api/sessions/${encodeURIComponent(sessionId)}/cancel?background=0`), {
          method: 'POST',
          headers: authHeaders(),
          credentials: 'include',
          keepalive: true,
        }).catch(() => { /* 切换会话失败时仍只断开本地流 */ })
      }
      this.pendingContinuations = []
      abortController?.abort()
    },

    async openSession(sessionId: string): Promise<void> {
      this.stopStreamOnly()
      activeStream += 1
      this.streaming = false
      this.sessionId = sessionId
      try {
        const data = await api<HistoryData>(
          `/api/sessions/${encodeURIComponent(sessionId)}/messages`,
        )
        this.messages = mapHistory(data.messages || [])
        this.turns = data.turns || 0
        this.setContext(data.tokens, data.max_tokens)
        this.setUsage(data.usage)
        this.pendingContinuations = []
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
        this.turns = 0
        this.steps = 0
        this.contextTokens = 0
        this.contextMax = 0
        this.resetUsage()
        this.todos = []
        this.bump()
        throw error
      }
      this.bump()
    },

    async newSession(workspaceId: string): Promise<void> {
      this.stopStreamOnly()
      activeStream += 1
      this.streaming = false
      const workspace = useWorkspaceStore()
      const session = await workspace.createSession(workspaceId)
      this.sessionId = session.id
      this.messages = []
      this.turns = 0
      this.steps = 0
      this.contextTokens = 0
      this.contextMax = 0
      this.resetUsage()
      this.todos = []
      this.pendingContinuations = []
      this.bump()
    },

    async send(message: string, workspaceId?: string, options: SendOptions = {}): Promise<void> {
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
      if (options.internal) {
        if (!this.sessionId) return
        this.messages.push({
          kind: 'note',
          id: newId('note'),
          text: '后台任务已完成，继续处理…',
        })
      } else {
        this.messages.push({
          kind: 'user',
          id: newId('user'),
          content: text,
          attachments: options.attachments || [],
        })
      }
      this.messages.push(createAssistantTurn())
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
            attachments: options.attachments?.map((item) => item.path) || [],
            internal: Boolean(options.internal),
            process_id: options.processId || '',
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
                } else if (data.append) {
                  assistant.workEvents.push({ kind: 'thought', id, text })
                } else {
                  // 完整 thought 事件可能晚于流式增量到达；同文只保留一条。
                  const clean = text.trim()
                  if (!clean) break
                  const key = thoughtKey(clean)
                  const duplicate = assistant.workEvents.some(
                    (event) => event.kind === 'thought' && thoughtKey(event.text) === key,
                  )
                  if (!duplicate) {
                    assistant.workEvents.push({ kind: 'thought', id, text: clean })
                  }
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
              case 'tool_output': {
                const name = String(data.name || 'run_command')
                const id = data.id ? String(data.id) : undefined
                const target = findOpenToolEvent(assistant.workEvents, id, name)
                if (target) {
                  const chunk = String(data.text || '')
                  target.liveOutput = `${target.liveOutput || ''}${chunk}`.slice(-120_000)
                  this.bump()
                }
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
                  event.liveOutput = ''
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
                this.turns = done.turns ?? this.turns
                this.setContext(done.tokens, done.max_tokens)
                this.setUsage(done.session_usage)
                this.setTurnUsage(done.usage)
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
          window.setTimeout(() => { void this.drainContinuations() }, 0)
        }
      }
    },

    queueProcessContinuation(record: ProcessInfo): void {
      if (!record.notify || !record.session_id || record.session_id !== this.sessionId) return
      if (this.pendingContinuations.some((item) => item.id === record.id)) return
      this.pendingContinuations.push(record)
      if (!this.streaming) void this.drainContinuations()
    },

    async drainContinuations(): Promise<void> {
      if (this.streaming) return
      const record = this.pendingContinuations.shift()
      if (!record) return
      const workspace = useWorkspaceStore()
      const workspaceId = workspace.activeWorkspaceId || undefined
      const exit = record.exit_code === null || record.exit_code === undefined
        ? 'unknown'
        : String(record.exit_code)
      const prompt = [
        '[Background process update]',
        `Process: ${record.id}`,
        `Command: ${record.command}`,
        `Terminal: ${record.terminal_label}`,
        `Exit code: ${exit}`,
        `Duration: ${record.duration_seconds.toFixed(1)}s`,
        '',
        record.log_tail ? `Last output:\n${record.log_tail}` : 'No output was captured.',
        '',
        'Continue the task using this result. If the process failed, inspect the output and fix the cause.',
        'If it succeeded, verify the result and report completion. Do not repeat work that already succeeded.',
      ].join('\n')
      await this.send(prompt, workspaceId, {
        internal: true,
        processId: record.id,
      })
    },

    async compactContext(): Promise<void> {
      if (this.streaming || !this.sessionId) return
      const ui = useUiStore()
      try {
        const data = await api<CompactData>(
          `/api/sessions/${encodeURIComponent(this.sessionId)}/compact`,
          { method: 'POST' },
        )
        this.setContext(data.tokens, data.max_tokens)
        this.setUsage(data.usage)
        this.turns = data.turns ?? this.turns
        this.messages.push({
          kind: 'note',
          id: newId('note'),
          text: data.compacted
            ? '上下文已压缩，较早的对话折叠为摘要'
            : '当前没有可压缩的历史',
        })
        this.bump()
      } catch (error) {
        ui.showToast(errorMessage(error), true)
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
