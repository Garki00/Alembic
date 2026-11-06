import type { ApprovalRequest } from '../api/types'

export type ApprovalStatus = 'pending' | 'once' | 'always' | 'denied' | 'expired'

export interface ChatApproval extends ApprovalRequest {
  status: ApprovalStatus
  submitting: boolean
}

export interface ToolCallView {
  name: string
  args: Record<string, unknown>
}

export interface ToolResultView {
  name: string
  content: string
}

export interface UserTurn {
  kind: 'user'
  id: string
  content: string
}

export interface AssistantTurn {
  kind: 'assistant'
  id: string
  content: string
  toolCalls: ToolCallView[]
  toolResults: ToolResultView[]
  approvals: ChatApproval[]
  error: string
  streaming: boolean
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
    toolCalls: [],
    toolResults: [],
    approvals: [],
    error: '',
    streaming: false,
  }
}
