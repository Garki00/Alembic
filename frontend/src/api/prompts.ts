// Named system prompts used by the settings "custom" panel.

import { api } from './client'

export interface PromptItem {
  id: string
  name: string
  content: string
  created_at: string
  updated_at: string
}

export interface PromptState {
  selected: string
  prompts: PromptItem[]
}

export function getPrompts(): Promise<PromptState> {
  return api<PromptState>('/api/prompts')
}

export function createPrompt(name: string, content: string): Promise<PromptState> {
  return api<PromptState>('/api/prompts', { method: 'POST', body: { name, content } })
}

export function updatePrompt(id: string, name: string, content: string): Promise<PromptState> {
  return api<PromptState>(`/api/prompts/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: { name, content },
  })
}

export function deletePrompt(id: string): Promise<PromptState> {
  return api<PromptState>(`/api/prompts/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

/** An empty id selects the built-in default prompt. */
export function selectPrompt(id: string): Promise<PromptState> {
  return api<PromptState>('/api/prompts/select', { method: 'POST', body: { id } })
}
