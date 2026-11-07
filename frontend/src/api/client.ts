// 后端 API 的统一入口。
//
// - 默认走同源 /api（开发时由 Vite 代理到 FastAPI）；
// - 设置 VITE_API_BASE_URL 可直连后端，适合前后端分别部署；
// - 若后端启用了 ALEMBIC_TOKEN，前端会从 ?token= 读取并改用 Bearer 头。

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/+$/, '')
const TOKEN_STORAGE_KEY = 'alembic-token'
const LEGACY_TOKEN_STORAGE_KEY = 'smart-agent-token'  // 旧版本，读取后迁移

let authToken = ''

export function initAuthToken(): void {
  if (typeof window === 'undefined') return
  const url = new URL(window.location.href)
  const fromQuery = url.searchParams.get('token')
  if (fromQuery) {
    authToken = fromQuery
    try {
      window.sessionStorage.setItem(TOKEN_STORAGE_KEY, fromQuery)
    } catch (_) { /* 隐私模式可能禁用 storage */ }
    // 别把 token 留在地址栏里
    url.searchParams.delete('token')
    const next = `${url.pathname}${url.search}${url.hash}`
    window.history.replaceState(window.history.state, '', next)
    return
  }
  try {
    const current = window.sessionStorage.getItem(TOKEN_STORAGE_KEY)
    const legacy = window.sessionStorage.getItem(LEGACY_TOKEN_STORAGE_KEY)
    authToken = current || legacy || ''
    if (!current && legacy) {
      window.sessionStorage.setItem(TOKEN_STORAGE_KEY, legacy)
      window.sessionStorage.removeItem(LEGACY_TOKEN_STORAGE_KEY)
    }
  } catch (_) {
    authToken = ''
  }
}

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`
}

export function authHeaders(): Record<string, string> {
  return authToken ? { Authorization: `Bearer ${authToken}` } : {}
}

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

interface ApiOptions extends Omit<RequestInit, 'body'> {
  body?: unknown
}

export async function api<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const headers = new Headers(options.headers)
  headers.set('Accept', 'application/json')
  if (options.body !== undefined && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  for (const [key, value] of Object.entries(authHeaders())) headers.set(key, value)

  const response = await fetch(apiUrl(path), {
    credentials: 'include',
    ...options,
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  })

  if (!response.ok) {
    let message = response.statusText || `HTTP ${response.status}`
    try {
      const data = await response.json()
      if (data && typeof data.detail === 'string' && data.detail) message = data.detail
    } catch (_) { /* 忽略非 JSON 错误体 */ }
    throw new ApiError(message, response.status)
  }
  if (response.status === 204) return null as T
  return (await response.json()) as T
}

export function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message
  return String(error || '未知错误')
}
