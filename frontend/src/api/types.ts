// 与 web/server.py 的数据结构保持一致（字段名使用后端的 snake_case）。

export type ModelProvider =
  | 'deepseek'
  | 'openai'
  | 'anthropic'
  | 'claude'
  | 'xai'
  | 'google'
  | 'moonshot'
  | 'zhipu'
  | 'qwen'
  | 'volcengine'
  | 'minimax'
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
  supports_reasoning: boolean
  supports_images: boolean
  context_length: number
  max_output_tokens: number
}

export interface ProviderInfo {
  id: string
  provider: ModelProvider | string
  name: string
  base_url: string
  /** `openai` | `anthropic` | "" for the provider's default protocol. */
  api_mode: string
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
  supports_reasoning?: boolean
  supports_images?: boolean
  context_length?: number
  max_output_tokens?: number
}

export interface ProviderPayload {
  id?: string | null
  provider: string
  name: string
  base_url: string
  api_mode?: string
  api_key: string
  models: ProviderModelPayload[]
}

/** Runtime view of the active model (provider config plus the selected model). */
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
  supports_reasoning: boolean
  supports_images: boolean
  context_length: number
  max_output_tokens: number
  updated_at: string
  api_key_set: boolean
  api_key_hint: string
}

export type ToolFieldKind = 'text' | 'number' | 'select' | 'switch' | 'radio'

export interface ToolFieldInfo {
  key: string
  label: string
  secret: boolean
  multiline: boolean
  placeholder: string
  kind: ToolFieldKind
  options: Array<{ value: string; label: string }>
  required: boolean
  enabled_when?: { key: string; value: string } | null
  // 由 ToolStore.public() 补齐
  value: string
  value_set: boolean
  hint: string
}

export interface ToolCapability {
  entry: string
  read_only: boolean
  destructive: boolean
  side_effect_scope: string
  risk_level: 'low' | 'medium' | 'high' | 'critical' | string
  needs_approval: boolean
  always_ask: boolean
  allowed_in_plan_mode: boolean
  permission_name: string
}

export interface ToolInfo {
  source?: 'builtin' | 'plugin'
  source_id?: string
  name: string
  label: string
  description: string
  risk: 'read' | 'write' | 'exec' | string
  capability?: ToolCapability
  tool_names: string[]
  default_enabled: boolean
  experimental?: boolean
  enabled: boolean
  fields: ToolFieldInfo[]
}

export type PermissionBehavior = 'allow' | 'ask' | 'deny'

export interface PermissionRuleInfo {
  tool: string
  content?: string
}

export interface PermissionRulesetInfo {
  version?: number
  rules: Record<PermissionBehavior, PermissionRuleInfo[]>
}

export interface PermissionRulesResponse {
  rules: PermissionRulesetInfo
  project_rules: PermissionRulesetInfo
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
  pinned?: boolean
  archived?: boolean
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
  kind: 'text' | 'markdown' | 'image' | 'unsupported'
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
  /** 第一次启动（供应商配置文件尚不存在）才为 true，用于决定是否弹出初始化向导。 */
  first_run?: boolean
  tools: ToolInfo[]
  workspaces: WorkspaceInfo[]
  active_workspace_id: string
  sessions: SessionSummary[]
  preferences: Preferences
  terminals: TerminalInfo[]
  sandbox?: SandboxStatus
  sandbox_config?: SandboxConfig
}

export interface SandboxStatus {
  ready: boolean
  backend: string
  platform: string
  detail: string
  network_modes: string[]
  allowed_domains: string[]
  reasons: string[]
}

export interface SandboxConfig {
  version: number
  network: 'offline' | 'allowlist' | string
  allowed_domains: string[]
  allowed_ports: number[]
  proxy_port: number
}

export type TranscriptViewMode = 'compact' | 'standard' | 'detailed' | 'verbose'
export type CollapseTiming = 'completion' | 'next_input'
export type PermissionMode = 'plan' | 'build' | 'edit' | 'yolo'
export type TerminalKind = 'powershell' | 'cmd' | 'gitbash' | 'wsl'
export type LinkTarget = 'sidebar' | 'browser'
export type ThemeMode = 'light' | 'dark' | 'system'

export interface TerminalInfo {
  value: TerminalKind
  label: string
  available: boolean
  detail: string
}

export interface Preferences {
  theme: ThemeMode
  transcript_view: TranscriptViewMode
  collapse_timing: CollapseTiming
  permission_mode: PermissionMode
  terminal: TerminalKind
  link_target: LinkTarget
  developer_tools: boolean
  /** Keep the first exchange verbatim when the context is compacted. */
  compact_keep_first_turn: boolean
  /** Built-in subagent type -> `providerId/modelId`; empty inherits the active model. */
  subagent_models: Record<string, string>
}

export interface HistoryMessage {
  role: 'user' | 'assistant' | 'tool' | 'system' | string
  content: string
  /** 消息时间（epoch 毫秒），仅新版本会话记录 */
  time?: number
  reasoning?: string
  tool_calls?: Array<{ id?: string; name: string; args: Record<string, unknown> }>
  tool_name?: string
  tool_call_id?: string
  internal?: boolean
}

/** 提供方上报的用量；缓存读/写单独成桶，未上报时为 null */
export interface UsageInfo {
  /** 本轮内的模型请求次数 */
  attempts?: number
  /** 会话累计请求次数 */
  requests?: number
  turns?: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number | null
  cache_write_tokens: number | null
  reasoning_tokens?: number | null
  prompt_tokens: number
  total_tokens: number
  /** 0..1；提供方没有上报缓存桶时为 null */
  hit_rate: number | null
  cache_reported_requests?: number
}

export interface HistoryData {
  session_id: string
  messages: HistoryMessage[]
  turns: number
  compactions: number
  tokens?: number
  token_source?: 'provider' | 'local' | string
  max_tokens?: number
  usage?: UsageInfo
}

export interface ProcessInfo {
  id: string
  command: string
  cwd: string
  workspace: string
  session_id: string
  description: string
  terminal: string
  terminal_label: string
  status: 'running' | 'completed' | 'failed' | 'stopped' | string
  running: boolean
  exit_code: number | null
  started_at: string
  ended_at: string
  duration_seconds: number
  notify: boolean
  log_path: string
  pid: number
  sandboxed?: boolean
  sandbox_backend?: string
  network?: string
  /** Only carried by process_completed events; used for internal continuation hints. */
  log_tail?: string
}

export interface ProcessListData {
  processes: ProcessInfo[]
  running: number
  finished: number
}

export interface ProcessDetailData {
  process: ProcessInfo
  log: string
}

export interface SubagentInfo {
  id: string
  parent_session_id: string
  agent_type: string
  description: string
  prompt?: string
  child_session_id: string
  status: 'running' | 'completed' | 'failed' | 'stopped' | string
  background: boolean
  started_at: number
  finished_at: number | null
  report: string
  error: string
  usage: Record<string, unknown>
  tool_uses: number
  duration_ms: number
  output_file: string
  model: string
  allowed_tools: string[]
  notified: boolean
}

export interface SubagentListData {
  subagents: SubagentInfo[]
}

export interface SubagentDetailData {
  subagent: SubagentInfo & { messages?: HistoryMessage[] }
}

export interface AttachmentInfo {
  name: string
  path: string
  size: number
  kind: 'file' | 'image' | 'document' | string
}

export interface CompactData {
  compacted: boolean
  tokens: number
  token_source: 'provider' | 'local' | string
  max_tokens: number
  messages: number
  turns: number
  usage?: UsageInfo
}

export interface BrowseResult {
  path: string
  parent: string | null
  dirs: Array<{ name: string; path: string }>
}

export interface PickFolderResult {
  path: string
  cancelled: boolean
}

export interface UpstreamModelMeta {
  context_length?: number
  max_output_tokens?: number
  supports_images?: boolean
  supports_reasoning?: boolean
}

export interface FetchModelsResponse {
  base_url: string
  models: string[]
  metadata?: Record<string, UpstreamModelMeta>
}

export type ApprovalDecision = 'once' | 'always' | 'deny'

export interface ApprovalRequest {
  id: string
  tool: string
  risk: 'write' | 'exec' | string
  target: string
  /** Plain-language summary of what this request is about to do. */
  summary: string
  /** Raw command or file content, expanded on demand. */
  preview: string
  /** Step-by-step breakdown of the command (only set for command-style tools). */
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
  max_tokens?: number
  messages?: number
  turns?: number
  /** 本轮的用量（含缓存桶） */
  usage?: UsageInfo
  /** 会话累计用量 */
  session_usage?: UsageInfo
}
