import { defineStore } from 'pinia'
import { api } from '../api/client'
import type {
  ActiveModelInfo,
  BootstrapData,
  BrowseResult,
  FetchModelsResponse,
  Preferences,
  PickFolderResult,
  ProviderInfo,
  ProviderPayload,
  SandboxConfig,
  SandboxStatus,
  SessionSummary,
  TerminalInfo,
  ThinkingLevel,
  ToolInfo,
  WorkspaceInfo,
} from '../api/types'

interface ToolUpdatePayload {
  enabled?: boolean
  config?: Record<string, string>
  clear?: string[]
}

const DEFAULT_PREFERENCES: Preferences = {
  theme: 'light',
  transcript_view: 'detailed',
  collapse_timing: 'completion',
  permission_mode: 'workspace_write',
  terminal: 'powershell',
  link_target: 'sidebar',
  developer_tools: false,
  compact_keep_first_turn: false,
}

export const useWorkspaceStore = defineStore('workspace', {
  state: () => ({
    providers: [] as ProviderInfo[],
    activeProviderId: '',
    activeModelId: '',
    activeModel: null as ActiveModelInfo | null,
    tools: [] as ToolInfo[],
    workspaces: [] as WorkspaceInfo[],
    activeWorkspaceId: '',
    sessions: [] as SessionSummary[],
    terminals: [] as TerminalInfo[],
    preferences: { ...DEFAULT_PREFERENCES } as Preferences,
    sandbox: null as SandboxStatus | null,
    sandboxConfig: null as SandboxConfig | null,
    needsSetup: false,
    firstRun: false,
    ready: false,
  }),

  getters: {
    activeWorkspace(state): WorkspaceInfo | null {
      return state.workspaces.find((item) => item.id === state.activeWorkspaceId)
        ?? state.workspaces[0]
        ?? null
    },

    activeProvider(state): ProviderInfo | null {
      return state.providers.find((item) => item.id === state.activeProviderId)
        ?? state.providers[0]
        ?? null
    },

    activeModelInfo(state): ActiveModelInfo | null {
      return state.activeModel
    },

    /** 是否有真正可用的供应商（有密钥、有 Base URL、至少一个模型）。 */
    hasProvider(state): boolean {
      return state.providers.some(
        (provider) => provider.api_key_set && Boolean(provider.base_url) && provider.models.length > 0,
      )
    },

    sessionsOf(state) {
      return (workspaceId: string): SessionSummary[] => {
        const fallback = state.workspaces[0]?.id ?? ''
        return state.sessions.filter((item) => (item.workspace_id || fallback) === workspaceId)
      }
    },
  },

  actions: {
    async bootstrap(): Promise<void> {
      const data = await api<BootstrapData>('/api/bootstrap')
      this.providers = data.providers || []
      this.activeProviderId = data.active_provider_id || ''
      this.activeModelId = data.active_model_id || ''
      this.activeModel = data.active_model || null
      this.tools = data.tools || []
      this.workspaces = data.workspaces || []
      this.activeWorkspaceId = data.active_workspace_id || this.workspaces[0]?.id || ''
      this.sessions = data.sessions || []
      this.terminals = data.terminals || []
      this.preferences = data.preferences || { ...DEFAULT_PREFERENCES }
      this.sandbox = data.sandbox || null
      this.sandboxConfig = data.sandbox_config || null
      this.needsSetup = Boolean(data.needs_setup)
      this.firstRun = Boolean(data.first_run)
      this.ready = true
    },

    async refreshSessions(): Promise<void> {
      const data = await api<{ sessions: SessionSummary[] }>('/api/sessions')
      this.sessions = data.sessions || []
    },

    async refreshSandbox(): Promise<void> {
      const status = await api<SandboxStatus>('/api/sandbox/status')
      this.sandbox = status
      const data = await api<{ config: SandboxConfig }>('/api/sandbox/config')
      this.sandboxConfig = data.config
    },

    async saveSandboxConfig(patch: Partial<SandboxConfig>): Promise<void> {
      const data = await api<{ config: SandboxConfig }>('/api/sandbox/config', {
        method: 'PUT',
        body: patch,
      })
      this.sandboxConfig = data.config
    },

    applyProviders(data: {
      providers: ProviderInfo[]
      active_provider_id: string
      active_model_id: string
      active_model: ActiveModelInfo | null
    }): void {
      this.providers = data.providers || []
      this.activeProviderId = data.active_provider_id || ''
      this.activeModelId = data.active_model_id || ''
      this.activeModel = data.active_model || null
      this.needsSetup = !this.providers.some(
        (provider) => provider.api_key_set && provider.base_url && provider.models.length,
      )
    },

    async fetchProviders(): Promise<void> {
      const data = await api<{
        providers: ProviderInfo[]
        active_provider_id: string
        active_model_id: string
        active_model: ActiveModelInfo | null
      }>('/api/providers')
      this.applyProviders(data)
    },

    async createSession(workspaceId: string): Promise<SessionSummary> {
      const data = await api<{ session: SessionSummary }>('/api/sessions', {
        method: 'POST',
        body: { workspace_id: workspaceId },
      })
      this.sessions = [data.session, ...this.sessions]
      this.activeWorkspaceId = workspaceId
      return data.session
    },

    async deleteSession(sessionId: string): Promise<void> {
      await api(`/api/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' })
      this.sessions = this.sessions.filter((item) => item.id !== sessionId)
    },

    async updateSession(
      sessionId: string,
      patch: { title?: string; pinned?: boolean; archived?: boolean },
    ): Promise<SessionSummary> {
      const data = await api<{ session: SessionSummary }>(
        `/api/sessions/${encodeURIComponent(sessionId)}`,
        { method: 'PATCH', body: patch },
      )
      const index = this.sessions.findIndex((item) => item.id === sessionId)
      if (index >= 0) this.sessions.splice(index, 1, data.session)
      else this.sessions.unshift(data.session)
      return data.session
    },

    async branchSession(sessionId: string): Promise<SessionSummary> {
      const data = await api<{ session: SessionSummary }>(
        `/api/sessions/${encodeURIComponent(sessionId)}/branch`,
        { method: 'POST' },
      )
      this.sessions = [data.session, ...this.sessions]
      return data.session
    },

    async activateWorkspace(workspaceId: string): Promise<void> {
      this.activeWorkspaceId = workspaceId
      try {
        await api(`/api/workspaces/${encodeURIComponent(workspaceId)}/activate`, { method: 'POST' })
      } catch (_) {
        // 记录当前工作区失败不影响本次使用
      }
    },

    async addWorkspace(path: string): Promise<WorkspaceInfo> {
      const data = await api<{
        workspace: WorkspaceInfo
        workspaces: WorkspaceInfo[]
        active_id: string
      }>('/api/workspaces', { method: 'POST', body: { path } })
      this.workspaces = data.workspaces || this.workspaces
      this.activeWorkspaceId = data.active_id || this.activeWorkspaceId
      return data.workspace
    },

    async renameWorkspace(workspaceId: string, name: string): Promise<void> {
      const data = await api<{ workspaces: WorkspaceInfo[] }>(
        `/api/workspaces/${encodeURIComponent(workspaceId)}`,
        { method: 'PATCH', body: { name } },
      )
      this.workspaces = data.workspaces || this.workspaces
    },

    async deleteWorkspace(workspaceId: string): Promise<number> {
      const data = await api<{
        removed_sessions: number
        workspaces: WorkspaceInfo[]
        active_id: string
      }>(`/api/workspaces/${encodeURIComponent(workspaceId)}`, { method: 'DELETE' })
      this.workspaces = data.workspaces || []
      this.activeWorkspaceId = data.active_id || this.workspaces[0]?.id || ''
      await this.refreshSessions()
      return data.removed_sessions || 0
    },

    async browseWorkspace(path?: string): Promise<BrowseResult> {
      const query = path ? `?path=${encodeURIComponent(path)}` : ''
      return api<BrowseResult>(`/api/workspaces/browse${query}`)
    },

    async pickWorkspaceFolder(initial = ''): Promise<PickFolderResult> {
      return api<PickFolderResult>('/api/workspaces/pick', {
        method: 'POST',
        body: { initial },
      })
    },

    async saveProvider(payload: ProviderPayload): Promise<ProviderInfo> {
      const data = await api<{
        provider: ProviderInfo
        providers: ProviderInfo[]
        active_provider_id: string
        active_model_id: string
        active_model: ActiveModelInfo | null
      }>('/api/providers', {
        method: 'POST',
        body: payload,
      })
      this.applyProviders(data)
      return data.provider
    },

    async deleteProvider(providerId: string): Promise<void> {
      const data = await api<{
        providers: ProviderInfo[]
        active_provider_id: string
        active_model_id: string
        active_model: ActiveModelInfo | null
      }>(`/api/providers/${encodeURIComponent(providerId)}`, { method: 'DELETE' })
      this.applyProviders(data)
    },

    async activateModel(providerId: string, modelId: string): Promise<void> {
      const data = await api<{
        providers: ProviderInfo[]
        active_provider_id: string
        active_model_id: string
        active_model: ActiveModelInfo | null
      }>(`/api/providers/${encodeURIComponent(providerId)}/activate`, {
        method: 'POST',
        body: { model_id: modelId },
      })
      this.applyProviders(data)
    },

    async fetchUpstreamModels(payload: {
      provider_id?: string
      base_url: string
      api_key: string
      provider: string
    }): Promise<FetchModelsResponse> {
      return api<FetchModelsResponse>('/api/providers/fetch', {
        method: 'POST',
        body: payload,
      })
    },

    async setThinking(level: ThinkingLevel): Promise<void> {
      const model = this.activeModel
      if (!model) return
      const data = await api<{
        providers: ProviderInfo[]
        active_provider_id: string
        active_model_id: string
        active_model: ActiveModelInfo | null
      }>(`/api/providers/${encodeURIComponent(model.provider_id)}/models/${encodeURIComponent(model.id)}/thinking`, {
        method: 'PATCH',
        body: { thinking: level },
      })
      this.applyProviders(data)
    },

    async updateTool(name: string, payload: ToolUpdatePayload): Promise<void> {
      const data = await api<{ tools: ToolInfo[] }>(`/api/tools/${encodeURIComponent(name)}`, {
        method: 'PUT',
        body: payload,
      })
      this.tools = data.tools || this.tools
    },

    async savePreferences(patch: Partial<Preferences>): Promise<void> {
      const data = await api<{ preferences: Preferences }>('/api/preferences', {
        method: 'PUT',
        body: patch,
      })
      this.preferences = data.preferences
    },

    async fetchTools(): Promise<void> {
      const data = await api<{ tools: ToolInfo[] }>('/api/tools')
      this.tools = data.tools || this.tools
    },
  },
})
