import { ApiError, apiUrl, authHeaders, errorDetail } from './client'

export interface ChatPayload {
  session_id?: string | null
  message: string
  workspace_id?: string
  attachments?: string[]
  internal?: boolean
  process_id?: string
  subagent_id?: string
  /** Per-turn permission mode; omitted means the saved preference. */
  permission_mode?: string
}

export type ChatEventHandler = (event: string, data: Record<string, unknown>) => void

/** Start one chat turn and deliver SSE events one by one. */
export async function streamChat(
  payload: ChatPayload,
  onEvent: ChatEventHandler,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(apiUrl('/api/chat'), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      ...authHeaders(),
    },
    credentials: 'include',
    body: JSON.stringify({
      session_id: payload.session_id || undefined,
      message: payload.message,
      workspace_id: payload.workspace_id || undefined,
      attachments: payload.attachments || [],
      internal: Boolean(payload.internal),
      process_id: payload.process_id || '',
      subagent_id: payload.subagent_id || '',
      permission_mode: payload.permission_mode || '',
    }),
    signal,
  })

  if (!response.ok) {
    let message = response.statusText || `HTTP ${response.status}`
    try {
      const data = await response.json()
      message = errorDetail(data) || message
    } catch (_) { /* 忽略非 JSON 错误体 */ }
    throw new ApiError(message, response.status)
  }
  if (!response.body) throw new Error('当前浏览器不支持流式响应')

  await readSSE(response.body.getReader(), onEvent)
}

/** Subscribe to background process status events. */
export async function streamProcessEvents(
  onEvent: ChatEventHandler,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(apiUrl('/api/processes/events'), {
    method: 'GET',
    headers: {
      Accept: 'text/event-stream',
      ...authHeaders(),
    },
    credentials: 'include',
    signal,
  })
  if (!response.ok) {
    throw new ApiError(response.statusText || `HTTP ${response.status}`, response.status)
  }
  if (!response.body) throw new Error('当前浏览器不支持流式响应')
  await readSSE(response.body.getReader(), onEvent)
}

/** Subscribe to subagent lifecycle events (progress, completion, background approvals). */
export async function streamSubagentEvents(
  onEvent: ChatEventHandler,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(apiUrl('/api/subagents/events'), {
    method: 'GET',
    headers: {
      Accept: 'text/event-stream',
      ...authHeaders(),
    },
    credentials: 'include',
    signal,
  })
  if (!response.ok) {
    throw new ApiError(response.statusText || `HTTP ${response.status}`, response.status)
  }
  if (!response.body) throw new Error('当前浏览器不支持流式响应')
  await readSSE(response.body.getReader(), onEvent)
}

async function readSSE(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  onEvent: ChatEventHandler,
): Promise<void> {
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n?/g, '\n')
      let index = buffer.indexOf('\n\n')
      while (index >= 0) {
        const raw = buffer.slice(0, index)
        buffer = buffer.slice(index + 2)
        let event = 'message'
        let data = ''
        for (const line of raw.split('\n')) {
          if (line.startsWith('event:')) event = line.slice(6).trim()
          else if (line.startsWith('data:')) data += line.slice(5).trim()
        }
        if (data) onEvent(event, JSON.parse(data) as Record<string, unknown>)
        index = buffer.indexOf('\n\n')
      }
    }
  } finally {
    reader.cancel().catch(() => { /* 已经结束时取消失败可忽略 */ })
  }
}
