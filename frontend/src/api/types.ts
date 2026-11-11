// 与 web/server.py 的数据结构保持一致（字段名使用后端的 snake_case）。

export type ModelProvider =
  | 'deepseek'
  | 'openai'
  | 'claude'
  | 'qwen'
  | 'siliconflow'
  | 'custom'

export type ThinkingLevel = 'none' | 'low' | 'high' | 'max'

export interface ProviderModelInfo {
  id: string
  model: string
  name: string
  thinking: ThinkingLevel
  thinking_levels: ThinkingLevel[]
  temperature: number
}

export interface ProviderInfo {
  id: string
  provider: ModelProvider | string
  name: string
  base_url: string
  api_key_set: boolean
  api_key_hint: string
  models: ProviderModelInfo[]
  updated_at: string
}

export interface ProviderModelPayload {
  id?: string | null
  model: string
  name?: string
  thinking: ThinkingLevel
  thinking_levels: ThinkingLevel[]
  temperature?: number
}

export interface ProviderPayload {
  id?: string | null
  provider: string
  name: string
  base_url: string
  api_key: string
  models: ProviderModelPayload[]
}

/** 当前激活模型的运行时视图（供应商配置 + 选中的模型）。 */
export interface ActiveModelInfo {
  id: string
  name: string
  provider: string
  provider_id: string
  provider_name: string
  model: string
  base_url: string
  temperature: number
  thinking: ThinkingLevel
  thinking_levels: ThinkingLevel[]
  updated_at: string
  api_key_set: boolean
  api_key_hint: string
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

export interface WorkspaceEntry {
  name: string
  path: string
  type: 'file' | 'dir'
  size: number
  mtime: string
}

export interface WorkspaceDirListing {
  path: string
  entries: WorkspaceEntry[]
  truncated: boolean
}

export interface WorkspaceFilePreview {
  path: string
  name: string
  size: number
  supported: boolean
  kind: 'text' | 'markdown' | 'unsupported'
  content: string
  truncated: boolean
  reason?: string
}

export interface BootstrapData {
  providers: ProviderInfo[]
  active_provider_id: string
  active_model_id: string
  active_model: ActiveModelInfo | null
  needs_setup: boolean
  tools: ToolInfo[]
  workspaces: WorkspaceInfo[]
  active_workspace_id: string
  sessions: SessionSummary[]
  preferences: Preferences
  terminals: TerminalInfo[]
}

export type TranscriptViewMode = 'compact' | 'standard' | 'detailed' | 'verbose'
export type CollapseTiming = 'completion' | 'next_input'
export type PermissionMode = 'read_only' | 'approval_all' | 'workspace_write' | 'full_access'
export type TerminalKind = 'powershell' | 'cmd' | 'gitbash' | 'wsl'
export type LinkTarget = 'sidebar' | 'browser'

export interface TerminalInfo {
  value: TerminalKind
  label: string
  available: boolean
  detail: string
}

export interface Preferences {
  transcript_view: TranscriptViewMode
  collapse_timing: CollapseTiming
  permission_mode: PermissionMode
  terminal: TerminalKind
  link_target: LinkTarget
  developer_tools: boolean
}

export interface HistoryMessage {
  role: 'user' | 'assistant' | 'tool' | 'system' | string
  content: string
  reasoning?: string
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
  models: string[]
}

export type ApprovalDecision = 'once' | 'always' | 'deny'

export interface ApprovalRequest {
  id: string
  tool: string
  risk: 'write' | 'exec' | string
  target: string
  /** 人话说明：这条请求准备做什么。 */
  summary: string
  /** 原始命令 / 文件内容，按需展开。 */
  preview: string
  /** 命令拆出的分步说明（仅命令类工具有值）。 */
  steps?: string[]
  reason?: string
  rule?: string
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
