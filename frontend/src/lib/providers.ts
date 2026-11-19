/**
 * Model provider templates.
 *
 * All provider-related UI (setup wizard, Settings → Providers) is derived from here:
 * - baseUrl / hint feed form autofill and help text;
 * - modelPlaceholder / modelExamples are template metadata only; the UI does not
 *   show "common models" options;
 * - iconPaths are brand marks in a 24×24 viewBox;
 * - every provider except Anthropic speaks an OpenAI-compatible protocol;
 *   Anthropic uses its native Messages API so explicit cache breakpoints work.
 *   The cache policy itself is resolved from the model id on the backend.
 */

export type ApiMode = 'openai' | 'anthropic'

export interface ProviderTemplate {
  id: string
  /** Display name shown in the UI (Chinese for some built-in providers). */
  name: string
  /** Short English name, e.g. "DeepSeek". */
  short: string
  baseUrl: string
  modelPlaceholder: string
  modelExamples: string[]
  hint: string
  color: string
  background: string
  iconPaths: string[]
  /** If true, the icon is rendered as a stroke (custom providers). */
  stroke?: boolean
  /** Wire protocols this provider can speak; the first one is the default. */
  apiModes?: ApiMode[]
  /** Base URL used when the Anthropic protocol is selected. */
  anthropicBaseUrl?: string
  /** Base URL used when the OpenAI protocol differs from `baseUrl`. */
  openaiBaseUrl?: string
}

export const PROVIDERS: ProviderTemplate[] = [
  {
    id: 'deepseek',
    name: '深度求索',
    short: 'DeepSeek',
    baseUrl: 'https://api.deepseek.com',
    anthropicBaseUrl: 'https://api.deepseek.com/anthropic',
    apiModes: ['openai', 'anthropic'],
    modelPlaceholder: '例如 deepseek-chat',
    modelExamples: ['deepseek-chat', 'deepseek-reasoner'],
    hint: 'DeepSeek 官方 API',
    color: '#4d6bfe',
    background: '#eef1ff',
    iconPaths: [
      'M23.748 4.482c-.254-.124-.364.113-.512.234-.051.039-.094.09-.137.136-.372.397-.806.657-1.373.626-.829-.046-1.537.214-2.163.848-.133-.782-.575-1.248-1.247-1.548-.352-.156-.708-.311-.955-.65-.172-.241-.219-.51-.305-.774-.055-.16-.11-.323-.293-.35-.2-.031-.278.136-.356.276-.313.572-.434 1.202-.422 1.84.027 1.436.633 2.58 1.838 3.393.137.093.172.187.129.323-.082.28-.18.552-.266.833-.055.179-.137.217-.329.14a5.526 5.526 0 0 1-1.736-1.18c-.857-.828-1.631-1.742-2.597-2.458a11.365 11.365 0 0 0-.689-.471c-.985-.957.13-1.743.388-1.836.27-.098.093-.432-.779-.428-.872.004-1.67.295-2.687.684a3.055 3.055 0 0 1-.465.137 9.597 9.597 0 0 0-2.883-.102c-1.885.21-3.39 1.102-4.497 2.623C.082 8.606-.231 10.684.152 12.85c.403 2.284 1.569 4.175 3.36 5.653 1.858 1.533 3.997 2.284 6.438 2.14 1.482-.085 3.133-.284 4.994-1.86.47.234.962.327 1.78.397.63.059 1.236-.03 1.705-.128.735-.156.684-.837.419-.961-2.155-1.004-1.682-.595-2.113-.926 1.096-1.296 2.746-2.642 3.392-7.003.05-.347.007-.565 0-.845-.004-.17.035-.237.23-.256a4.173 4.173 0 0 0 1.545-.475c1.396-.763 1.96-2.015 2.093-3.517.02-.23-.004-.467-.247-.588zM11.581 18c-2.089-1.642-3.102-2.183-3.52-2.16-.392.024-.321.471-.235.763.09.288.207.486.371.739.114.167.192.416-.113.603-.673.416-1.842-.14-1.897-.167-1.361-.802-2.5-1.86-3.301-3.307-.774-1.393-1.224-2.887-1.298-4.482-.02-.386.093-.522.477-.592a4.696 4.696 0 0 1 1.529-.039c2.132.312 3.946 1.265 5.468 2.774.868.86 1.525 1.887 2.202 2.891.72 1.066 1.494 2.082 2.48 2.914.348.292.625.514.891.677-.802.09-2.14.11-3.054-.614z',
    ],
  },
  {
    id: 'openai',
    name: 'OpenAI',
    short: 'OpenAI',
    baseUrl: 'https://api.openai.com/v1',
    modelPlaceholder: '例如 gpt-4.1-mini',
    modelExamples: ['gpt-4.1', 'gpt-4.1-mini', 'gpt-4o-mini'],
    hint: 'OpenAI 官方 API',
    color: '#10a37f',
    background: '#e9f7f2',
    iconPaths: [
      'M9.205 8.658v-2.26c0-.19.072-.333.238-.428l4.543-2.616c.619-.357 1.356-.523 2.117-.523 2.854 0 4.662 2.212 4.662 4.566 0 .167 0 .357-.024.547l-4.71-2.759a.797.797 0 0 0-.856 0l-5.97 3.473zm10.609 8.8V12.06c0-.333-.143-.57-.429-.737l-5.97-3.473 1.95-1.118a.433.433 0 0 1 .476 0l4.543 2.617c1.309.76 2.189 2.378 2.189 3.948 0 1.808-1.07 3.473-2.76 4.163zM7.802 12.703l-1.95-1.142c-.167-.095-.239-.238-.239-.428V5.899c0-2.545 1.95-4.472 4.591-4.472 1 0 1.927.333 2.712.928L8.23 5.067c-.285.166-.428.404-.428.737v6.898zM12 15.128l-2.795-1.57v-3.33L12 8.658l2.795 1.57v3.33L12 15.128zm1.796 7.23c-1 0-1.927-.332-2.712-.927l4.686-2.712c.285-.166.428-.404.428-.737v-6.898l1.974 1.142c.167.095.238.238.238.428v5.233c0 2.545-1.974 4.472-4.614 4.472zm-5.637-5.303l-4.544-2.617c-1.308-.761-2.188-2.378-2.188-3.948A4.482 4.482 0 0 1 4.21 6.327v5.423c0 .333.143.571.428.738l5.947 3.449-1.95 1.118a.432.432 0 0 1-.476 0zm-.262 3.9c-2.688 0-4.662-2.021-4.662-4.519 0-.19.024-.38.047-.57l4.686 2.71c.286.167.571.167.856 0l5.97-3.448v2.26c0 .19-.07.333-.237.428l-4.543 2.616c-.619.357-1.356.523-2.117.523zm5.899 2.83a5.947 5.947 0 0 0 5.827-4.756C22.287 18.339 24 15.84 24 13.296c0-1.665-.713-3.282-1.998-4.448.119-.5.19-.999.19-1.498 0-3.401-2.759-5.947-5.946-5.947-.642 0-1.26.095-1.88.31A5.962 5.962 0 0 0 10.205 0a5.947 5.947 0 0 0-5.827 4.757C1.713 5.447 0 7.945 0 10.49c0 1.666.713 3.283 1.998 4.448-.119.5-.19 1-.19 1.499 0 3.401 2.759 5.946 5.946 5.946.642 0 1.26-.095 1.88-.309a5.96 5.96 0 0 0 4.162 1.713z',
    ],
  },
  {
    id: 'anthropic',
    name: 'Anthropic Claude',
    short: 'Claude',
    baseUrl: 'https://api.anthropic.com',
    openaiBaseUrl: 'https://api.anthropic.com/v1',
    apiModes: ['anthropic', 'openai'],
    modelPlaceholder: '例如 claude-sonnet-4-5',
    modelExamples: ['claude-sonnet-4-5', 'claude-opus-4-1', 'claude-haiku-4-5'],
    hint: '原生 Messages API，支持显式缓存断点',
    color: '#c96442',
    background: '#fbf0ea',
    iconPaths: [
      'M13.827 3.52h3.603L24 20h-3.603l-6.57-16.48zm-7.258 0h3.767L16.906 20h-3.674l-1.343-3.461H5.017l-1.344 3.46H0L6.57 3.522zm4.132 9.959L8.453 7.687 6.205 13.48H10.7z',
    ],
  },
  {
    id: 'qwen',
    name: '阿里云千问',
    short: 'Qwen',
    baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    modelPlaceholder: '例如 qwen-plus',
    modelExamples: ['qwen-plus', 'qwen-max', 'qwen-turbo'],
    hint: 'DashScope 兼容模式',
    color: '#615ced',
    background: '#f0efff',
    iconPaths: [
      'M19.713 2.825h-5.907c0-.251-.066-.503-.196-.728L12.819.728A1.46 1.46 0 0 0 11.559 0h-1.58c-.52 0-1 .275-1.26.728l-2.42 4.19a1.45 1.45 0 0 0 0 1.453l.791 1.37c.26.448.74.727 1.26.727h11.36c.52 0 1-.275 1.26-.728l.79-1.37a1.45 1.45 0 0 0 0-1.451l-.79-1.37a1.46 1.46 0 0 0-1.26-.728zM.195 9.905l2.951 5.115a1.46 1.46 0 0 0-.531.531l-.791 1.37a1.45 1.45 0 0 0 0 1.452l.79 1.37c.26.448.74.727 1.26.727h4.84c.52 0 1-.275 1.26-.728l.79-1.37a1.45 1.45 0 0 0 0-1.451L5.086 7.083a1.46 1.46 0 0 0-1.26-.728h-1.58c-.52 0-1 .276-1.26.728l-.79 1.37a1.45 1.45 0 0 0 0 1.452m15.888 13.367 2.951-5.115c.217.125.469.196.728.196h1.582c.52 0 1-.275 1.26-.728l.79-1.37a1.45 1.45 0 0 0 0-1.451l-2.42-4.191a1.46 1.46 0 0 0-1.259-.728h-1.582c-.52 0-1 .276-1.259.728l-5.678 9.838a1.45 1.45 0 0 0 0 1.452l.79 1.37c.26.448.74.727 1.26.727h1.582c.52 0 1-.275 1.26-.728z',
    ],
  },
  {
    id: 'siliconflow',
    name: '硅基流动',
    short: 'SiliconFlow',
    baseUrl: 'https://api.siliconflow.cn/v1',
    modelPlaceholder: '例如 deepseek-ai/DeepSeek-V3',
    modelExamples: ['deepseek-ai/DeepSeek-V3', 'Qwen/Qwen3-235B-A22B'],
    hint: 'SiliconCloud 托管模型',
    color: '#0f9d8a',
    background: '#e9f7f4',
    iconPaths: [
      'M22.956 6.521H12.522c-.577 0-1.044.468-1.044 1.044v3.13c0 .577-.466 1.044-1.043 1.044H1.044c-.577 0-1.044.467-1.044 1.044v4.174C0 17.533.467 18 1.044 18h10.434c.577 0 1.044-.467 1.044-1.043v-3.13c0-.578.466-1.044 1.043-1.044h9.391c.577 0 1.044-.467 1.044-1.044V7.565c0-.576-.467-1.044-1.044-1.044z',
    ],
  },
  {
    id: 'xai',
    name: 'xAI',
    short: 'xAI',
    baseUrl: 'https://api.x.ai/v1',
    modelPlaceholder: '例如 grok-4',
    modelExamples: ['grok-4', 'grok-3', 'grok-3-mini'],
    hint: 'xAI 官方 API',
    color: '#1f2937',
    background: '#f1f2f4',
    iconPaths: [
      'M2.8 3h4.6l5 6.7L17.8 3H21l-7.1 9.2L21.4 21h-4.6l-5.3-7.1L5.9 21H2.7l7.4-9.6z',
    ],
  },
  {
    id: 'google',
    name: 'Google Gemini',
    short: 'Gemini',
    baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai',
    modelPlaceholder: '例如 gemini-2.5-pro',
    modelExamples: ['gemini-2.5-pro', 'gemini-2.5-flash'],
    hint: 'Google AI Studio 官方 API',
    color: '#4285f4',
    background: '#eaf1fe',
    iconPaths: [
      'M12 2.6a9.4 9.4 0 1 0 0 18.8 9.4 9.4 0 0 0 0-18.8zm0 3.4a6 6 0 1 1 0 12 6 6 0 0 1 0-12z',
      'M12.6 10.7h9.2v3.1h-9.2z',
    ],
  },
  {
    id: 'moonshot',
    name: '月之暗面',
    short: 'Moonshot',
    baseUrl: 'https://api.moonshot.cn/v1',
    anthropicBaseUrl: 'https://api.moonshot.cn/anthropic',
    apiModes: ['openai', 'anthropic'],
    modelPlaceholder: '例如 kimi-k2-0905-preview',
    modelExamples: ['kimi-k2-0905-preview', 'moonshot-v1-128k'],
    hint: 'Moonshot 官方 API',
    color: '#4b5563',
    background: '#f1f2f4',
    iconPaths: [
      'M15.8 2.3a9.7 9.7 0 1 0 5.9 16.1A8.5 8.5 0 0 1 15.8 2.3z',
    ],
  },
  {
    id: 'zhipu',
    name: '智谱',
    short: 'Zhipu',
    baseUrl: 'https://open.bigmodel.cn/api/paas/v4',
    anthropicBaseUrl: 'https://open.bigmodel.cn/api/anthropic',
    apiModes: ['openai', 'anthropic'],
    modelPlaceholder: '例如 glm-4.6',
    modelExamples: ['glm-4.6', 'glm-4.5-air'],
    hint: '智谱开放平台 API',
    color: '#1d4ed8',
    background: '#e8eefc',
    iconPaths: [
      'M12 2 22 12 12 22 2 12z',
      'M12 7.4 16.6 12 12 16.6 7.4 12z',
    ],
  },
  {
    id: 'volcengine',
    name: '火山引擎',
    short: 'Volcengine',
    baseUrl: 'https://ark.cn-beijing.volces.com/api/v3',
    modelPlaceholder: '例如 doubao-seed-1-6-250615',
    modelExamples: ['doubao-seed-1-6-250615', 'doubao-1-5-pro-32k'],
    hint: '方舟平台 API，可用推理接入点 ID',
    color: '#1664ff',
    background: '#e9f0ff',
    iconPaths: [
      'M2.6 5.2h4.3L12 15l5.1-9.8h4.3L12 21.4z',
    ],
  },
  {
    id: 'minimax',
    name: 'MiniMax',
    short: 'MiniMax',
    baseUrl: 'https://api.minimax.chat/v1',
    anthropicBaseUrl: 'https://api.minimax.chat/anthropic',
    apiModes: ['openai', 'anthropic'],
    modelPlaceholder: '例如 MiniMax-M1',
    modelExamples: ['MiniMax-M1', 'abab6.5s-chat'],
    hint: 'MiniMax 官方 API',
    color: '#e8590c',
    background: '#feeceb',
    iconPaths: [
      'M3 20.4V3.6h3.4l5.6 8 5.6-8H21v16.8h-3.4V9.4L12 17.2 6.4 9.4v11z',
    ],
  },
  {
    id: 'custom',
    name: '自定义供应商',
    short: 'Custom',
    baseUrl: '',
    apiModes: ['openai', 'anthropic'],
    modelPlaceholder: '填写上游模型 ID',
    modelExamples: [],
    hint: 'OpenAI 或 Anthropic 兼容接口，需填 Base URL',
    color: '#65735f',
    background: '#f1f2ec',
    stroke: true,
    iconPaths: [
      'M4 8h5M15 8h5M4 16h5M15 16h5',
      'M9 8a2 2 0 1 0 4 0 2 2 0 0 0-4 0M11 16a2 2 0 1 0 4 0 2 2 0 0 0-4 0',
    ],
  },
]

export const CUSTOM_PROVIDER = PROVIDERS[PROVIDERS.length - 1]

/** Older saved providers used ``claude``; the template id is ``anthropic``. */
const PROVIDER_ALIASES: Record<string, string> = { claude: 'anthropic' }

export function providerOf(id: string): ProviderTemplate {
  const raw = (id || '').trim().toLowerCase()
  const key = PROVIDER_ALIASES[raw] || raw
  return PROVIDERS.find((item) => item.id === key) ?? {
    ...CUSTOM_PROVIDER,
    id: key || 'custom',
    name: key || CUSTOM_PROVIDER.name,
    short: key || CUSTOM_PROVIDER.short,
  }
}

export function providerName(id: string): string {
  return providerOf(id).name
}

/** Wire protocols this provider can speak; the first entry is the default. */
export function providerApiModes(id: string): ApiMode[] {
  return providerOf(id).apiModes ?? ['openai']
}

export function providerDefaultApiMode(id: string): ApiMode {
  return providerApiModes(id)[0] ?? 'openai'
}

/** Default base URL for one protocol of a provider. */
export function providerBaseUrl(id: string, mode: ApiMode): string {
  const template = providerOf(id)
  if (mode === 'anthropic') return template.anthropicBaseUrl || template.baseUrl
  return template.openaiBaseUrl || template.baseUrl
}
