// 与 web/server.py 的数据结构保持一致（字段名使用后端的 snake_case）。

export type ModelProvider =
  | 'deepseek'
  | 'openai'
  | 'claude'
  | 'qwen'
  | 'siliconflow'
  | 'custom'

export interface ModelInfo {
  id: string
  name: string
  provider: ModelProvider | string
  model: string
  base_url: string
  temperature: number
  updated_at: string
  api_key_set: boolean
  api_key_hint: string
}

export interface ModelPayload {
  id?: string | null
  name: string
  provider: string
  model: string
  base_url: string
  api_key: string
  temperature: number
}

export type ToolFieldKind = 'text' | 'number' | 'select' | 'switch'

export interface ToolFieldInfo {
  key: string
  label: string
  secret: boolean
  multiline: boolean
  placeholder: string
  kind: ToolFieldKind
  options: Array<{ value: string; label: string }>
  required: boolean
  // 由 ToolStore.public() 补齐
  value: string
  value_set: boolean
  hint: string
}

export interface ToolInfo {
  name: string
  label: string
  description: string
  risk: 'read' | 'write' | 'exec' | string
  tool_names: string[]
  default_enabled: boolean
  enabled: boolean
  fields: ToolFieldInfo[]
}

export interface WorkspaceInfo {
  id: string
  name: string
  path: string
  created_at?: string
}

export interface SessionSummary {
  id: string
  title: string
  turns: number
  messages: number
  updated_at: string
  compactions: number
  workspace_id: string
}

export interface BootstrapData {
  models: ModelInfo[]
  active_id: string
  active_model: ModelInfo | null
  needs_setup: boolean
  tools: ToolInfo[]
  workspaces: WorkspaceInfo[]
  active_workspace_id: string
  sessions: SessionSummary[]
  preferences: Preferences
}

export type TranscriptViewMode = 'compact' | 'standard' | 'detailed' | 'verbose'
export type CollapseTiming = 'completion' | 'next_input'

export interface Preferences {
  transcript_view: TranscriptViewMode
  collapse_timing: CollapseTiming
  developer_tools: boolean
}

export interface HistoryMessage {
  role: 'user' | 'assistant' | 'tool' | 'system' | string
  content: string
  tool_calls?: Array<{ id?: string; name: string; args: Record<string, unknown> }>
  tool_name?: string
  tool_call_id?: string
}

export interface HistoryData {
  session_id: string
  messages: HistoryMessage[]
  turns: number
  compactions: number
}

export interface BrowseResult {
  path: string
  parent: string | null
  dirs: Array<{ name: string; path: string }>
}

export interface FetchModelsResponse {
  base_url: string
  fetched: string[]
  added: string[]
  skipped: string[]
  models: ModelInfo[]
  active_id: string
}

export type ApprovalDecision = 'once' | 'always' | 'deny'

export interface ApprovalRequest {
  id: string
  tool: string
  risk: 'write' | 'exec' | string
  target: string
  summary: string
  preview: string
}

export interface QuestionRequest {
  id: string
  question: string
  options: string[]
  allow_free_text: boolean
}

export type TodoStatus = 'pending' | 'in_progress' | 'completed'

export interface TodoItem {
  content: string
  status: TodoStatus | string
}

export interface TodosData {
  session_id: string
  items: TodoItem[]
}

export interface ChatDoneEvent {
  session_id: string
  text: string
  tokens?: number
  token_source?: 'provider' | 'local' | string
  messages?: number
  turns?: number
}
