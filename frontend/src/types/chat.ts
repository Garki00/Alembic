import type { ApprovalRequest, QuestionRequest } from '../api/types'

export type ApprovalStatus = 'pending' | 'once' | 'always' | 'denied' | 'expired'

export interface ChatApproval extends ApprovalRequest {
  status: ApprovalStatus
  submitting: boolean
}

export type QuestionStatus = 'pending' | 'answered' | 'expired'

export interface ChatQuestion extends QuestionRequest {
  status: QuestionStatus
  answer: string
  submitting: boolean
}

export interface ToolCallView {
  id?: string
  name: string
  args: Record<string, unknown>
}

export interface ToolResultView {
  id?: string
  name: string
  content: string
}

export type SubagentStatus = 'running' | 'completed' | 'failed' | 'stopped'

export interface SubagentStep {
  id: string
  phase: string
  label: string
  detail: string
}

export interface SubagentView {
  /** agentId returned by the backend. */
  id: string
  /** Tool call that launched this run; used to nest the row under the tool event. */
  toolCallId: string
  agentType: string
  description: string
  status: SubagentStatus
  background: boolean
  model: string
  thinking: string
  steps: SubagentStep[]
  report: string
  error: string
  outputFile: string
  toolUses: number
  durationMs: number
  usage: Record<string, unknown>
}

export type WorkEvent =
  | { kind: 'thought'; id: string; text: string }
  | {
      kind: 'tool'
      id: string
      name: string
      args: Record<string, unknown>
      result: string
      error: boolean
      liveOutput?: string
    }
  | {
      kind: 'subagent'
      id: string
      agentId: string
      agentType: string
      description: string
      status: 'completed' | 'failed' | 'stopped'
      report: string
      error: string
      toolUses: number
      durationMs: number
      outputFile: string
      usage: Record<string, unknown>
    }

export interface UserAttachment {
  name: string
  path: string
  size: number
  kind: string
}

export interface UserTurn {
  kind: 'user'
  id: string
  content: string
  attachments?: UserAttachment[]
  /** 发送时间（epoch 毫秒） */
  createdAt?: number
}

export interface AssistantTurn {
  kind: 'assistant'
  id: string
  content: string
  workEvents: WorkEvent[]
  toolCalls: ToolCallView[]
  toolResults: ToolResultView[]
  approvals: ChatApproval[]
  questions: ChatQuestion[]
  subagents: SubagentView[]
  praiseLines: string[]
  error: string
  streaming: boolean
  stepsOpen: boolean
  stepsManual: boolean
  /** 本轮开始时间（用户发出消息的时间） */
  startedAt?: number
  /** 本轮结束时间；流式过程中为空 */
  finishedAt?: number
}

export interface NoteTurn {
  kind: 'note'
  id: string
  text: string
}

export type ChatTurn = UserTurn | AssistantTurn | NoteTurn

export function isAssistantTurn(turn: ChatTurn): turn is AssistantTurn {
  return turn.kind === 'assistant'
}

let counter = 0
export function newId(prefix = 'turn'): string {
  counter += 1
  return `${prefix}-${Date.now().toString(36)}-${counter}`
}

export function createAssistantTurn(): AssistantTurn {
  return {
    kind: 'assistant',
    id: newId('assistant'),
    content: '',
    workEvents: [],
    toolCalls: [],
    toolResults: [],
    approvals: [],
    questions: [],
    subagents: [],
    praiseLines: [],
    error: '',
    streaming: false,
    stepsOpen: true,
    stepsManual: false,
  }
}
