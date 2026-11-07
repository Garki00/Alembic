import { defineStore } from 'pinia'
import { api } from '../api/client'
import type {
  BootstrapData,
  BrowseResult,
  FetchModelsResponse,
  ModelInfo,
  ModelPayload,
  Preferences,
  SessionSummary,
  ToolInfo,
  WorkspaceInfo,
} from '../api/types'

interface ToolUpdatePayload {
  enabled?: boolean
  config?: Record<string, string>
  clear?: string[]
}

const DEFAULT_PREFERENCES: Preferences = {
  transcript_view: 'detailed',
  collapse_timing: 'completion',
  developer_tools: false,
}

export const useWorkspaceStore = defineStore('workspace', {
  state: () => ({
    models: [] as ModelInfo[],
    activeModelId: '',
    activeModel: null as ModelInfo | null,
    tools: [] as ToolInfo[],
    workspaces: [] as WorkspaceInfo[],
    activeWorkspaceId: '',
    sessions: [] as SessionSummary[],
    preferences: { ...DEFAULT_PREFERENCES } as Preferences,
    needsSetup: false,
    ready: false,
  }),

  getters: {
    activeWorkspace(state): WorkspaceInfo | null {
      return state.workspaces.find((item) => item.id === state.activeWorkspaceId)
        ?? state.workspaces[0]
        ?? null
    },

    activeModelInfo(state): ModelInfo | null {
      return state.models.find((item) => item.id === state.activeModelId)
        ?? state.activeModel
        ?? null
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
      this.models = data.models || []
      this.activeModelId = data.active_id || ''
      this.activeModel = data.active_model || null
      this.tools = data.tools || []
      this.workspaces = data.workspaces || []
      this.activeWorkspaceId = data.active_workspace_id || this.workspaces[0]?.id || ''
      this.sessions = data.sessions || []
      this.preferences = data.preferences || { ...DEFAULT_PREFERENCES }
      this.needsSetup = Boolean(data.needs_setup)
      this.ready = true
    },

    async refreshSessions(): Promise<void> {
      const data = await api<{ sessions: SessionSummary[] }>('/api/sessions')
      this.sessions = data.sessions || []
    },

    async fetchModels(): Promise<void> {
      const data = await api<{ models: ModelInfo[]; active_id: string }>('/api/models')
      this.models = data.models || []
      this.activeModelId = data.active_id || ''
      this.activeModel = this.models.find((item) => item.id === this.activeModelId) ?? null
      this.needsSetup = !this.models.some(
        (model) => model.api_key_set && model.model && model.base_url,
      )
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

    async saveModel(payload: ModelPayload): Promise<ModelInfo> {
      const data = await api<{ model: ModelInfo; active_id: string }>('/api/models', {
        method: 'POST',
        body: payload,
      })
      this.activeModelId = data.active_id || this.activeModelId
      await this.fetchModels()
      return data.model
    },

    async deleteModel(modelId: string): Promise<void> {
      await api(`/api/models/${encodeURIComponent(modelId)}`, { method: 'DELETE' })
      await this.fetchModels()
    },

    async activateModel(modelId: string): Promise<void> {
      await api(`/api/models/${encodeURIComponent(modelId)}/activate`, { method: 'POST' })
      this.activeModelId = modelId
      this.activeModel = this.models.find((item) => item.id === modelId) ?? this.activeModel
    },

    async fetchUpstreamModels(payload: {
      base_url: string
      api_key: string
      provider: string
    }): Promise<FetchModelsResponse> {
      const data = await api<FetchModelsResponse>('/api/models/fetch', {
        method: 'POST',
        body: payload,
      })
      this.models = data.models || this.models
      this.activeModelId = data.active_id || this.activeModelId
      this.activeModel = this.models.find((item) => item.id === this.activeModelId) ?? null
      return data
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
