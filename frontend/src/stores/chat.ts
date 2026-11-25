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
  PermissionMode,
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
  type SubagentView,
  type UserAttachment,
  type WorkEvent,
} from '../types/chat'
import { useUiStore } from './ui'
import { useWorkspaceStore } from './workspace'

interface SendOptions {
  attachments?: UserAttachment[]
  internal?: boolean
  processId?: string
  subagentId?: string
}

/** A finished background subagent waiting for its continuation turn. */
export interface SubagentContinuation {
  id: string
  sessionId: string
  description: string
  status: string
}

/** 切走的对话在内存里的实时状态：包含正在流式输出的那一条。 */
export interface LiveSession {
  messages: ChatTurn[]
  turns: number
  steps: number
  contextTokens: number
  contextMax: number
  todos: TodoItem[]
  pendingContinuations: ProcessInfo[]
  streaming: boolean
  cacheReadTokens: number
  cachePromptTokens: number
  cacheHitRate: number | null
  cacheRequests: number
  turnCacheReadTokens: number
  turnPromptTokens: number
  turnHitRate: number | null
  turnAttempts: number
}

/** 侧栏状态图标：运行中 / 等待审批 / 等待选择。 */
export interface SessionStatus {
  running: boolean
  approval: boolean
  question: boolean
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

/** Newest subagent view for one agent id; a resumed agent gets a new run view. */
function findSubagent(turn: AssistantTurn, agentId: string): SubagentView | undefined {
  for (let index = turn.subagents.length - 1; index >= 0; index -= 1) {
    if (turn.subagents[index].id === agentId) return turn.subagents[index]
  }
  return undefined
}

function ensureSubagent(
  turn: AssistantTurn,
  data: Record<string, unknown>,
): SubagentView {
  const agentId = String(data.agent_id || '')
  const toolCallId = String(data.tool_call_id || '')
  const existing = findSubagent(turn, agentId)
  if (existing && (existing.status === 'running' || !toolCallId || existing.toolCallId === toolCallId)) {
    return existing
  }
  const view: SubagentView = {
    id: agentId,
    toolCallId,
    agentType: String(data.agent_type || ''),
    description: String(data.description || ''),
    status: 'running',
    background: Boolean(data.background),
    model: String(data.model || ''),
    thinking: String(data.thinking || ''),
    steps: [],
    report: '',
    error: '',
    outputFile: '',
    toolUses: 0,
    durationMs: 0,
    usage: {},
  }
  turn.subagents.push(view)
  return view
}

const SUBAGENT_PHASE_LABELS: Record<string, string> = {
  thinking: '思考',
  tool: '调用工具',
  tool_result: '工具结果',
  output: '命令输出',
  compacted: '压缩上下文',
  stopping: '停止中',
}

function appendSubagentStep(view: SubagentView, data: Record<string, unknown>): void {
  const phase = String(data.phase || 'progress')
  const callId = data.id ? String(data.id) : ''
  if (callId) {
    const existing = view.steps.find((step) => step.id === callId)
    if (existing) {
      if (phase === 'tool_result') {
        existing.detail = String(data.content || '').slice(0, 1200)
      }
      return
    }
  }
  const name = String(data.name || '')
  let detail = ''
  if (phase === 'tool') {
    detail = name
    const args = data.args
    if (args && typeof args === 'object') {
      const raw = JSON.stringify(args)
      if (raw && raw !== '{}') detail = `${name} ${raw.slice(0, 240)}`
    }
  } else if (phase === 'tool_result') {
    detail = String(data.content || '').slice(0, 1200)
  } else {
    detail = String(data.text || '').slice(0, 1200)
  }
  view.steps.push({
    id: callId || newId('substep'),
    phase,
    label: SUBAGENT_PHASE_LABELS[phase] || phase,
    detail,
  })
  if (view.steps.length > 80) view.steps.splice(0, view.steps.length - 80)
}

function completeSubagent(view: SubagentView, data: Record<string, unknown>): void {
  const status = String(data.status || 'completed')
  view.status = status === 'completed' || status === 'failed' || status === 'stopped'
    ? status
    : 'completed'
  view.report = String(data.report || '')
  view.error = String(data.error || '')
  view.outputFile = String(data.output_file || '')
  view.toolUses = Number(data.tool_uses || 0)
  view.durationMs = Number(data.duration_ms || 0)
  view.usage = (data.usage as Record<string, unknown>) || {}
  view.background = Boolean(data.background)
  if (data.model) view.model = String(data.model)
  if (data.thinking) view.thinking = String(data.thinking)
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

/** Restore the separate return step from a persisted subagent tool result. */
function addSubagentReturn(
  assistant: AssistantTurn,
  event: ToolWorkEvent,
  content: string,
): void {
  if (event.name !== 'agent') return
  if (!content.includes('[Subagent hand-back]')) return
  const agentId = /agentId:\s*(subagent-[A-Za-z0-9-]+)/.exec(content)?.[1] || ''
  const failed = content.startsWith('Error:')
  assistant.workEvents.push({
    kind: 'subagent',
    id: newId('subreturn'),
    agentId,
    agentType: '',
    description: String(event.args?.description || event.args?.subagent_type || ''),
    status: failed ? 'failed' : 'completed',
    report: content,
    error: '',
    toolUses: 0,
    durationMs: 0,
    outputFile: '',
    usage: {},
  })
}

export function mapHistory(items: HistoryMessage[]): ChatTurn[] {
  const turns: ChatTurn[] = []
  let assistant: AssistantTurn | null = null
  let lastUserTime: number | undefined

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
        createdAt: message.time,
      })
      lastUserTime = message.time
      continue
    }
    if (message.role === 'assistant') {
      if (!assistant) {
        assistant = createAssistantTurn()
        assistant.startedAt = lastUserTime
        turns.push(assistant)
      }
      if (message.time) {
        assistant.startedAt = assistant.startedAt ?? lastUserTime
        assistant.finishedAt = message.time
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
        addSubagentReturn(assistant, event, content)
      } else {
        const created: ToolWorkEvent = {
          kind: 'tool',
          id: message.tool_call_id || newId('tool'),
          name: message.tool_name || '工具',
          args: {},
          result: content,
          error: content.startsWith('Error:'),
        }
        assistant.workEvents.push(created)
        addSubagentReturn(assistant, created, content)
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
    draftWorkspaceId: null as string | null,
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
    pendingSubagentContinuations: [] as SubagentContinuation[],
    pendingPraise: [] as string[],
    // 正在流式输出的会话（全局同时只允许一个）
    runningSessionId: null as string | null,
    // 切走的会话的实时状态，回来时直接还原，避免再去拉不完整的服务端历史
    liveSessions: {} as Record<string, LiveSession>,
    // 侧栏状态图标
    sessionStatus: {} as Record<string, SessionStatus>,
    // 当前会话的权限模式；随请求发送，切回历史会话时恢复该会话自己的选择
    permissionMode: 'build' as PermissionMode,
    sessionModes: {} as Record<string, PermissionMode>,
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
    /** 全局是否还有一轮在跑（可能不在当前对话里） */
    busy(state): boolean {
      return state.runningSessionId !== null
    },
    /** 某个对话当前该显示的状态图标 */
    statusOf(state) {
      return (sessionId: string): SessionStatus | null => state.sessionStatus[sessionId] ?? null
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
      this.pendingSubagentContinuations = []
      this.pendingPraise = []
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
      this.pendingSubagentContinuations = []
      this.pendingPraise = []
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
      this.pendingSubagentContinuations = []
      this.pendingPraise = []
      abortController?.abort()
    },

    /** 把当前会话的实时状态存起来（只在运行中或有待审批/待选择时保留）。 */
    stashLive(): void {
      const sessionId = this.sessionId
      if (!sessionId) return
      const hasPending = this.messages.some((turn) => (
        turn.kind === 'assistant'
        && (turn.approvals.some((item) => item.status === 'pending')
          || turn.questions.some((item) => item.status === 'pending'))
      ))
      if (!this.streaming && !hasPending && this.runningSessionId !== sessionId) {
        delete this.liveSessions[sessionId]
        return
      }
      this.liveSessions[sessionId] = {
        messages: this.messages,
        turns: this.turns,
        steps: this.steps,
        contextTokens: this.contextTokens,
        contextMax: this.contextMax,
        todos: this.todos,
        pendingContinuations: this.pendingContinuations,
        streaming: this.streaming,
        cacheReadTokens: this.cacheReadTokens,
        cachePromptTokens: this.cachePromptTokens,
        cacheHitRate: this.cacheHitRate,
        cacheRequests: this.cacheRequests,
        turnCacheReadTokens: this.turnCacheReadTokens,
        turnPromptTokens: this.turnPromptTokens,
        turnHitRate: this.turnHitRate,
        turnAttempts: this.turnAttempts,
      }
    },

    /** 还原切走前的实时状态。 */
    applyLive(live: LiveSession): void {
      this.messages = live.messages
      this.turns = live.turns
      this.steps = live.steps
      this.contextTokens = live.contextTokens
      this.contextMax = live.contextMax
      this.todos = live.todos
      this.pendingContinuations = live.pendingContinuations
      this.streaming = live.streaming
      this.cacheReadTokens = live.cacheReadTokens
      this.cachePromptTokens = live.cachePromptTokens
      this.cacheHitRate = live.cacheHitRate
      this.cacheRequests = live.cacheRequests
      this.turnCacheReadTokens = live.turnCacheReadTokens
      this.turnPromptTokens = live.turnPromptTokens
      this.turnHitRate = live.turnHitRate
      this.turnAttempts = live.turnAttempts
    },

    /** 重新计算侧栏状态；没有状态时删除条目，图标随之消失。 */
    syncStatus(sessionId: string): void {
      const live = this.liveSessions[sessionId]
      const messages = sessionId === this.sessionId ? this.messages : live?.messages
      if (!messages) {
        delete this.sessionStatus[sessionId]
        return
      }
      let running = false
      let approval = false
      let question = false
      for (const turn of messages) {
        if (turn.kind !== 'assistant') continue
        if (turn.streaming) running = true
        if (turn.approvals.some((item) => item.status === 'pending')) approval = true
        if (turn.questions.some((item) => item.status === 'pending')) question = true
      }
      if (!running && !approval && !question) {
        delete this.sessionStatus[sessionId]
        return
      }
      this.sessionStatus[sessionId] = { running, approval, question }
    },

    async openSession(sessionId: string): Promise<void> {
      // 正在运行的流不随切换中止：先把当前会话的实时状态存起来。
      this.stashLive()
      // 未发消息的新对话占位保留：打开别的对话不会把它清掉。
      this.sessionId = sessionId
      const live = this.liveSessions[sessionId]
      if (live) {
        this.applyLive(live)
        // 已经跑完的快照用完就丢，下次重新拉服务端历史
        if (!live.streaming) delete this.liveSessions[sessionId]
        this.bump()
        return
      }
      this.streaming = false
      try {
        const data = await api<HistoryData>(
          `/api/sessions/${encodeURIComponent(sessionId)}/messages`,
        )
        // 拉取期间用户又切走了：这次结果作废，别覆盖当前对话
        if (this.sessionId !== sessionId) return
        this.messages = mapHistory(data.messages || [])
        this.turns = data.turns || 0
        this.setContext(data.tokens, data.max_tokens)
        this.setUsage(data.usage)
        this.pendingContinuations = []
      this.pendingSubagentContinuations = []
      this.pendingPraise = []
        try {
          const todos = await api<TodosData>(
            `/api/sessions/${encodeURIComponent(sessionId)}/todos`,
          )
          if (this.sessionId === sessionId) this.todos = todos.items || []
        } catch (_) {
          if (this.sessionId === sessionId) this.todos = []
        }
      } catch (error) {
        if (this.sessionId !== sessionId) return
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
      if (this.sessionId === sessionId) this.bump()
    },

    /** 切换当前会话的权限模式；同一会话后续消息沿用该选择。 */
    setPermissionMode(mode: PermissionMode): void {
      this.permissionMode = mode
      if (this.sessionId) this.sessionModes[this.sessionId] = mode
    },

    /** 进入会话时恢复该会话自己的模式；没选过就用设置里的默认值。 */
    restorePermissionMode(fallback: PermissionMode): void {
      const saved = this.sessionId ? this.sessionModes[this.sessionId] : undefined
      this.permissionMode = saved ?? fallback
    },

    async newSession(workspaceId: string): Promise<void> {
      this.stopStreamOnly()
      activeStream += 1
      this.streaming = false
      this.draftWorkspaceId = null
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
      this.pendingSubagentContinuations = []
      this.pendingPraise = []
      this.bump()
    },

    /**
     * 开始一个「还没发消息」的新对话。
     *
     * 全局只保留一个占位：同一个工作区重复点击不会再产生新行；点到别的工作区
     * 就把占位搬过去。真正落库要等用户发出第一条消息。
     */
    startDraft(workspaceId: string): void {
      if (!workspaceId) return
      if (this.draftWorkspaceId === workspaceId && !this.sessionId) return
      // 正在跑的会话先把实时状态存下来，切到新对话不等于取消它
      this.stashLive()
      this.sessionId = null
      this.draftWorkspaceId = workspaceId
      this.streaming = false
      this.resetConversation()
    },

    async send(message: string, workspaceId?: string, options: SendOptions = {}): Promise<void> {
      const text = message.trim()
      // 同一时刻只跑一轮：哪怕它属于另一个对话（切走后仍在跑）
      if ((!text && !options.subagentId) || this.streaming || this.runningSessionId) return

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
          this.draftWorkspaceId = null
        } catch (error) {
          ui.showToast(errorMessage(error), true)
          return
        }
      }

      // 先把消息推进响应式数组，再从数组里取回代理对象；
      // 直接改 createAssistantTurn() 返回的原始对象不会触发 Vue 更新。
      const startedAt = Date.now()
      if (options.internal) {
        if (!this.sessionId) return
        this.messages.push({
          kind: 'note',
          id: newId('note'),
          text: options.subagentId ? '子代理已完成，继续处理…' : '后台任务已完成，继续处理…',
        })
      } else {
        this.messages.push({
          kind: 'user',
          id: newId('user'),
          content: text,
          attachments: options.attachments || [],
          createdAt: startedAt,
        })
      }
      this.messages.push(createAssistantTurn())
      const assistant = this.messages[this.messages.length - 1] as AssistantTurn
      assistant.startedAt = startedAt
      assistant.streaming = true
      if (this.pendingPraise.length) {
        assistant.praiseLines.push(...this.pendingPraise)
        this.pendingPraise = []
      }
      this.streaming = true
      // 记录本轮属于哪个会话：切换对话时流继续跑，事件仍然写回这里。
      const streamSessionId = this.sessionId
      if (streamSessionId) {
        this.runningSessionId = streamSessionId
        this.syncStatus(streamSessionId)
      }
      this.bump()

      const streamId = ++activeStream
      const isCurrent = () => streamId === activeStream
      // 用户可能已经切到别的对话：状态要么写进当前会话，要么写进它自己的快照。
      const isActive = () => this.sessionId === streamSessionId
      const liveState = () => (streamSessionId ? this.liveSessions[streamSessionId] : undefined)
      const note = (text: string) => {
        const item = { kind: 'note' as const, id: newId('note'), text }
        if (isActive()) this.messages.push(item)
        else liveState()?.messages.push(item)
      }

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
            subagent_id: options.subagentId || '',
            permission_mode: this.permissionMode,
          },
          (event, data) => {
            if (!isCurrent()) return
            switch (event) {
              case 'meta': {
                const sid = String(data.session_id || '')
                if (!this.sessionId && sid) {
                  this.sessionId = sid
                  this.draftWorkspaceId = null
                  // 草稿发出后服务端才分配会话 ID：把当前模式记到该会话名下
                  this.sessionModes[sid] = this.permissionMode
                }
                if (sid) this.runningSessionId = sid
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
              case 'subagent_started': {
                const view = ensureSubagent(assistant, data)
                view.status = 'running'
                view.background = Boolean(data.background)
                if (data.model) view.model = String(data.model)
                if (data.thinking) view.thinking = String(data.thinking)
                if (data.tool_call_id) view.toolCallId = String(data.tool_call_id)
                this.bump()
                break
              }
              case 'subagent_progress': {
                if (String(data.phase || '') === 'text') break
                const view = findSubagent(assistant, String(data.agent_id || ''))
                if (!view) break
                appendSubagentStep(view, data)
                this.bump()
                break
              }
              case 'subagent_backgrounded': {
                const view = findSubagent(assistant, String(data.agent_id || ''))
                if (!view) break
                view.background = true
                view.status = 'running'
                this.bump()
                break
              }
              case 'subagent_completed': {
                const view = ensureSubagent(assistant, data)
                completeSubagent(view, data)
                assistant.workEvents.push({
                  kind: 'subagent',
                  id: newId('subreturn'),
                  agentId: view.id,
                  agentType: view.agentType,
                  description: view.description,
                  status: view.status === 'running' ? 'completed' : view.status,
                  report: view.report,
                  error: view.error,
                  toolUses: view.toolUses,
                  durationMs: view.durationMs,
                  outputFile: view.outputFile,
                  usage: view.usage,
                })
                this.bump()
                break
              }
              case 'praise': {
                const lines = Array.isArray(data.lines)
                  ? data.lines.map((line) => String(line)).filter(Boolean)
                  : []
                if (lines.length) {
                  assistant.praiseLines.push(...lines)
                  this.bump()
                }
                break
              }
              case 'approval': {
                const request = data as unknown as ApprovalRequest
                assistant.approvals.push({
                  ...request,
                  status: 'pending',
                  submitting: false,
                })
                if (streamSessionId) this.syncStatus(streamSessionId)
                this.bump()
                break
              }
              case 'approval_expired': {
                const id = String(data.id || '')
                const approval = assistant.approvals.find((item) => item.id === id)
                if (approval) {
                  approval.status = 'expired'
                  if (streamSessionId) this.syncStatus(streamSessionId)
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
                if (streamSessionId) this.syncStatus(streamSessionId)
                this.bump()
                break
              }
              case 'question_expired': {
                const id = String(data.id || '')
                const question = assistant.questions.find((item) => item.id === id)
                if (question) {
                  question.status = 'expired'
                  if (streamSessionId) this.syncStatus(streamSessionId)
                  this.bump()
                }
                break
              }
              case 'todo': {
                const items = (data.items as TodoItem[]) || []
                if (isActive()) this.todos = items
                else {
                  const live = liveState()
                  if (live) live.todos = items
                }
                this.bump()
                break
              }
              case 'compacted': {
                note('上下文已自动压缩，较早的对话折叠为摘要')
                this.bump()
                break
              }
              case 'done': {
                const done = data as unknown as ChatDoneEvent
                if (done.text !== undefined) finalText = done.text
                if (done.session_id && !this.sessionId) this.sessionId = done.session_id
                const live = liveState()
                if (isActive()) {
                  this.turns = done.turns ?? this.turns
                  this.setContext(done.tokens, done.max_tokens)
                  this.setUsage(done.session_usage)
                  this.setTurnUsage(done.usage)
                } else if (live) {
                  live.turns = done.turns ?? live.turns
                  if (typeof done.tokens === 'number') live.contextTokens = done.tokens
                  if (typeof done.max_tokens === 'number') live.contextMax = done.max_tokens
                }
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
          note('已停止生成')
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
          assistant.finishedAt = assistant.finishedAt ?? Date.now()
          // 只有当前正在看这个会话时才动它的界面状态
          if (isActive()) this.streaming = false
          const live = liveState()
          if (live) live.streaming = false
          if (this.runningSessionId === streamSessionId) this.runningSessionId = null
          if (streamSessionId) this.syncStatus(streamSessionId)
          abortController = null
          this.bump()
          // 后台任务续跑只属于这个会话，切走了就不要在别的对话里发
          if (isActive()) {
            window.setTimeout(() => {
              void this.drainContinuations()
              void this.drainSubagentContinuations()
            }, 0)
          }
        }
      }
    },

    queueProcessContinuation(record: ProcessInfo): void {
      if (!record.notify || !record.session_id) return
      if (record.session_id !== this.sessionId) {
        // 会话在后台跑完：记到它自己的待续列表里，切回去再续跑
        const live = this.liveSessions[record.session_id]
        if (!live) return
        if (!live.pendingContinuations.some((item) => item.id === record.id)) {
          live.pendingContinuations.push(record)
        }
        return
      }
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

    /** Queue a finished background subagent; the server injects its notification. */
    /** Attach a subagent note to the live turn, or hold it for the next one. */
    appendPraise(sessionId: string, lines: string[]): void {
      if (!sessionId || sessionId !== this.sessionId || !lines.length) return
      const assistant = [...this.messages]
        .reverse()
        .find((turn): turn is AssistantTurn => turn.kind === 'assistant')
      if (this.streaming && assistant?.streaming) {
        assistant.praiseLines.push(...lines)
        this.bump()
        return
      }
      this.pendingPraise.push(...lines)
    },

    queueSubagentContinuation(record: {
      id: string
      session_id: string
      description?: string
      status?: string
    }): void {
      if (!record.session_id || record.session_id !== this.sessionId) return
      if (this.pendingSubagentContinuations.some((item) => item.id === record.id)) return
      this.pendingSubagentContinuations.push({
        id: record.id,
        sessionId: record.session_id,
        description: record.description || '',
        status: record.status || 'completed',
      })
      if (!this.streaming && !this.runningSessionId) void this.drainSubagentContinuations()
    },

    async drainSubagentContinuations(): Promise<void> {
      if (this.streaming || this.runningSessionId) return
      const item = this.pendingSubagentContinuations.shift()
      if (!item) return
      // The notification may already have been delivered with a user message;
      // in that case the task is marked notified and no extra turn is needed.
      try {
        const data = await api<{ subagent?: { notified?: boolean } }>(
          `/api/subagents/${encodeURIComponent(item.id)}`,
        )
        if (data.subagent?.notified) {
          void this.drainSubagentContinuations()
          return
        }
      } catch (_) {
        return
      }
      const workspace = useWorkspaceStore()
      await this.send('', workspace.activeWorkspaceId || undefined, {
        internal: true,
        subagentId: item.id,
      })
    },

    async stopSubagent(agentId: string): Promise<void> {
      const ui = useUiStore()
      try {
        await api(`/api/subagents/${encodeURIComponent(agentId)}/stop`, { method: 'POST' })
        ui.showToast('已请求停止子代理')
      } catch (error) {
        ui.showToast(errorMessage(error), true)
      }
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
        if (this.sessionId) this.syncStatus(this.sessionId)
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
        if (this.sessionId) this.syncStatus(this.sessionId)
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
      if (this.sessionId) this.syncStatus(this.sessionId)
      ui.showToast(cleared ? `已清除 ${cleared} 条授权` : '本会话没有可清除的授权')
      return cleared
    },
  },
})
