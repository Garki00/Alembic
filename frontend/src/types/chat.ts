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
    error: '',
    streaming: false,
    stepsOpen: true,
    stepsManual: false,
  }
}
