import { api } from './client'
import type { PermissionRulesResponse, PermissionRulesetInfo } from './types'

/** Load user rules plus the active workspace's restricting rules. */
export function getPermissionRules(workspaceId: string): Promise<PermissionRulesResponse> {
  const query = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : ''
  return api<PermissionRulesResponse>(`/api/permissions${query}`)
}

/** Replace the user rule set; the backend validates tool names and content length. */
export function savePermissionRules(
  rules: PermissionRulesetInfo['rules'],
): Promise<{ rules: PermissionRulesetInfo }> {
  return api<{ rules: PermissionRulesetInfo }>('/api/permissions', {
    method: 'PUT',
    body: { rules },
  })
}
