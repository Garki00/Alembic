import { api } from './client'

export type ExtensionSource = 'builtin' | 'plugin'
export interface ExtensionDirectories { tools: string[]; mcp: string[]; skills: string[] }
export interface McpConfig {
  name: string
  enabled: boolean
  transport: 'stdio' | 'streamable_http' | 'sse'
  command?: string
  args?: string[]
  url?: string
  env?: Record<string, string>
  headers?: Record<string, string>
  oauth?: { redirect_uri: string; scope?: string } | null
  timeout?: number
}
export interface McpServer extends McpConfig {
  id: string
  source: ExtensionSource
  status?: string
  error?: string
  tools?: number
}
export interface ExtensionSkill {
  id: string
  name: string
  description: string
  source: ExtensionSource
  enabled: boolean
  path: string
  license?: string
  compatibility?: string
  error?: string
}
export interface ExtensionSnapshot {
  directories: ExtensionDirectories
  mcp: McpServer[]
  skills: ExtensionSkill[]
  errors: Array<{ path: string; message: string }>
}
export const getExtensions = () => api<ExtensionSnapshot>('/api/extensions')
export const refreshExtensions = () => api<ExtensionSnapshot>('/api/extensions/refresh', { method: 'POST' })
export interface DirectoryPickResult { path: string; cancelled: boolean }
export const saveDirectories = (directories: ExtensionDirectories) => api('/api/extensions/directories', { method: 'PUT', body: directories })
export const pickDirectory = () => api<DirectoryPickResult>('/api/extensions/pick-directory', { method: 'POST' })
export const saveMcp = (id: string, config: Partial<McpConfig>) => api(`/api/extensions/mcp/${encodeURIComponent(id)}`, { method: 'PUT', body: config })
export const deleteMcp = (id: string) => api(`/api/extensions/mcp/${encodeURIComponent(id)}`, { method: 'DELETE' })
export const setSkillEnabled = (id: string, enabled: boolean) => api(`/api/extensions/skills/${encodeURIComponent(id)}`, { method: 'PATCH', body: { enabled } })
