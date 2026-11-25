<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type {
  ApprovalDecision,
  AttachmentInfo,
  PermissionMode,
  ProviderInfo,
  ProviderModelInfo,
  ThinkingLevel,
} from '../api/types'
import { apiUrl, authHeaders, errorMessage } from '../api/client'
import { providerName } from '../lib/providers'
import { PERMISSION_MODES } from '../lib/preferences'
import {
  normalizeThinkingLevels,
  thinkingLabel,
  thinkingShort,
} from '../lib/thinking'
import { useChatStore } from '../stores/chat'
import { useUiStore } from '../stores/ui'
import { useWorkspaceStore } from '../stores/workspace'
import ApprovalCard from './ApprovalCard.vue'
import FloatingMenu from './FloatingMenu.vue'
import ProviderIcon from './ProviderIcon.vue'
import QuestionCard from './QuestionCard.vue'

const chat = useChatStore()
const workspace = useWorkspaceStore()
const ui = useUiStore()

const draft = ref('')
const textarea = ref<HTMLTextAreaElement | null>(null)
const switcher = ref<HTMLElement | null>(null)
const menuOpen = ref(false)
const menuProviderId = ref('')
const thinkingSwitcher = ref<HTMLElement | null>(null)
const thinkingOpen = ref(false)
const modeButton = ref<HTMLElement | null>(null)
const modeOpen = ref(false)
const toolsOpen = ref(false)
const toolsMenu = ref<HTMLElement | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const attachments = ref<AttachmentInfo[]>([])
const uploading = ref(false)
const compacting = ref(false)
const cacheOpen = ref(false)
const formEl = ref<HTMLElement | null>(null)
const shellEl = ref<HTMLElement | null>(null)
const hintEl = ref<HTMLElement | null>(null)
const statusEl = ref<HTMLElement | null>(null)
const hintSuppressed = ref(false)
let composerObserver: ResizeObserver | null = null

const activeProvider = computed(() => workspace.activeProvider)
const activeModelName = computed(
  () => workspace.activeModel?.name || workspace.activeModel?.model || '未配置模型',
)
const menuProvider = computed(
  () => workspace.providers.find((item) => item.id === menuProviderId.value)
    ?? workspace.activeProvider,
)
const menuModels = computed(() => menuProvider.value?.models ?? [])
const canSend = computed(
  () => !chat.busy && Boolean(draft.value.trim() || attachments.value.length),
)
/** 有没有真正可用的模型：没有时发送不会真正发出，只提示去配置。 */
/** 有审批或提问时，输入区换成请求卡片，底部状态行保持不变 */
const showRequest = computed(() => Boolean(chat.pendingApproval || chat.pendingQuestion))
const hasModel = computed(() => Boolean(workspace.activeModel) && workspace.hasProvider)
const noModelHint = ref(false)
let noModelTimer: number | undefined

function flashNoModel(): void {
  noModelHint.value = true
  if (noModelTimer !== undefined) window.clearTimeout(noModelTimer)
  noModelTimer = window.setTimeout(() => {
    noModelHint.value = false
    noModelTimer = undefined
  }, 3200)
}

const contextTitle = computed(() => {
  if (!chat.contextMax) return '上下文用量'
  const source = chat.contextTokens > 0 ? '' : '（尚无 API 用量，暂为 0）'
  return `上下文 ${chat.contextTokens} / ${chat.contextMax} tokens${source}`
})

/** 1_234_567 -> "1.2M"：状态行与缓存面板里用的紧凑格式 */
function compactTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`
  return String(value)
}

const thinkingState = computed(() => normalizeThinkingLevels(
  workspace.activeModel?.thinking_levels,
  workspace.activeModel?.thinking,
))
const thinkingLevels = computed(() => (
  workspace.activeModel?.supports_reasoning === false ? [] : thinkingState.value.levels
))
const currentThinking = computed(() => thinkingState.value.thinking)
const thinkingIndex = computed(() => Math.max(
  0,
  thinkingLevels.value.indexOf(currentThinking.value),
))

const currentModeOption = computed(
  () => PERMISSION_MODES.find((item) => item.value === chat.permissionMode)
    ?? PERMISSION_MODES[1],
)

const modelTitle = computed(() => {
  const model = workspace.activeModel
  if (!model) return '在设置里添加供应商与模型'
  const provider = workspace.providers.find((item) => item.id === model.provider_id)
  const label = provider ? providerTitle(provider) : providerName(model.provider)
  return `${label} · ${model.model}`
})

function providerTitle(provider: ProviderInfo): string {
  return provider.name || providerName(provider.provider)
}

function resize(): void {
  const element = textarea.value
  if (!element) return
  element.style.height = 'auto'
  element.style.height = `${Math.min(element.scrollHeight, 180)}px`
}

function send(): void {
  if (chat.streaming || !workspace.activeWorkspace) return
  // 没有可用模型时不发请求，只在底部提示一次，避免报错弹窗打断输入
  if (!hasModel.value) {
    flashNoModel()
    return
  }
  const text = draft.value.trim() || (attachments.value.length ? '请查看这些附件。' : '')
  if (!text) return
  const files = [...attachments.value]
  draft.value = ''
  attachments.value = []
  toolsOpen.value = false
  modeOpen.value = false
  void nextTick(() => resize())
  void chat.send(text, workspace.activeWorkspaceId || undefined, { attachments: files })
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    send()
  }
}

function toggleTools(): void {
  toolsOpen.value = !toolsOpen.value
  menuOpen.value = false
  thinkingOpen.value = false
  modeOpen.value = false
}

function toggleModeMenu(): void {
  modeOpen.value = !modeOpen.value
  menuOpen.value = false
  thinkingOpen.value = false
  toolsOpen.value = false
}

function chooseMode(mode: PermissionMode): void {
  if (mode === 'yolo' && chat.permissionMode !== 'yolo') {
    const confirmed = window.confirm(
      '免审批模式会让 Agent 无需审批执行普通工具和命令。确认切换吗？',
    )
    if (!confirmed) return
  }
  chat.setPermissionMode(mode)
  modeOpen.value = false
}

function chooseFile(): void {
  toolsOpen.value = false
  fileInput.value?.click()
}

function removeAttachment(index: number): void {
  attachments.value.splice(index, 1)
}

async function ensureSession(): Promise<string | null> {
  if (chat.sessionId) return chat.sessionId
  const workspaceId = workspace.activeWorkspaceId
  if (!workspaceId) return null
  const session = await workspace.createSession(workspaceId)
  chat.sessionId = session.id
  return session.id
}

async function uploadFile(file: File): Promise<void> {
  if (attachments.value.length >= 10) {
    ui.showToast('一次最多附加 10 个文件', true)
    return
  }
  if (file.size > 20_000_000) {
    ui.showToast(`${file.name} 超过 20 MB`, true)
    return
  }
  uploading.value = true
  try {
    const sessionId = await ensureSession()
    if (!sessionId) return
    const query = new URLSearchParams({
      filename: file.name,
      workspace_id: workspace.activeWorkspaceId || '',
    })
    const response = await fetch(
      apiUrl(`/api/sessions/${encodeURIComponent(sessionId)}/attachments?${query}`),
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/octet-stream',
          ...authHeaders(),
        },
        credentials: 'include',
        body: file,
      },
    )
    if (!response.ok) {
      let message = response.statusText || `HTTP ${response.status}`
      try {
        const data = await response.json()
        message = String(data.detail || message)
      } catch (_) { /* 非 JSON 错误体 */ }
      throw new Error(message)
    }
    const data = await response.json() as AttachmentInfo
    attachments.value.push(data)
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  } finally {
    uploading.value = false
  }
}

async function onFiles(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  input.value = ''
  for (const file of files) await uploadFile(file)
}

async function runCompact(): Promise<void> {
  if (compacting.value || chat.streaming) return
  toolsOpen.value = false
  compacting.value = true
  try {
    await chat.compactContext()
  } finally {
    compacting.value = false
  }
}

function decideApproval(decision: ApprovalDecision): void {
  const approval = chat.pendingApproval
  if (approval) void chat.decideApproval(approval, decision)
}

function answerQuestion(value: string): void {
  const question = chat.pendingQuestion
  if (question) void chat.answerQuestion(question, value)
}

function toggleMenu(): void {
  menuOpen.value = !menuOpen.value
  thinkingOpen.value = false
  modeOpen.value = false
  if (menuOpen.value) {
    menuProviderId.value = workspace.activeProviderId || workspace.providers[0]?.id || ''
  }
}

function toggleThinking(): void {
  thinkingOpen.value = !thinkingOpen.value
  menuOpen.value = false
  modeOpen.value = false
}

async function setThinking(level: ThinkingLevel): Promise<void> {
  const model = workspace.activeModel
  if (!model || level === currentThinking.value) return
  try {
    await workspace.setThinking(level)
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  }
}

function onThinkingSlide(event: Event): void {
  const index = Number((event.target as HTMLInputElement).value)
  const level = thinkingLevels.value[index]
  if (level) void setThinking(level)
}

function switchProvider(provider: ProviderInfo): void {
  menuProviderId.value = provider.id
}

async function switchModel(provider: ProviderInfo, model: ProviderModelInfo): Promise<void> {
  menuOpen.value = false
  thinkingOpen.value = false
  if (provider.id === workspace.activeProviderId && model.id === workspace.activeModelId) return
  try {
    await workspace.activateModel(provider.id, model.id)
    ui.showToast(`已切换到 ${model.name || model.model}`)
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  }
}

function openSettings(): void {
  menuOpen.value = false
  thinkingOpen.value = false
  ui.openSettings('models')
}

function onDocumentClick(event: MouseEvent): void {
  const target = event.target as Node | null
  if (menuOpen.value) {
    if (!(switcher.value && target && switcher.value.contains(target))) {
      menuOpen.value = false
    }
  }
  if (thinkingOpen.value) {
    if (!(thinkingSwitcher.value && target && thinkingSwitcher.value.contains(target))) {
      thinkingOpen.value = false
    }
  }
  if (toolsOpen.value) {
    if (!(toolsMenu.value && target && toolsMenu.value.contains(target))) {
      toolsOpen.value = false
    }
  }
}

function onDocumentKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    menuOpen.value = false
    thinkingOpen.value = false
    toolsOpen.value = false
    modeOpen.value = false
    cacheOpen.value = false
  }
}

watch(
  () => ui.composerFocusTick,
  () => {
    draft.value = ui.composerDraft
    void nextTick(() => {
      resize()
      textarea.value?.focus()
    })
  },
)

// 只聚焦：点侧栏的「新对话」占位时不清空已经打好的内容
watch(
  () => ui.composerFocusOnly,
  () => { void nextTick(() => textarea.value?.focus()) },
)

// 草稿按对话保存：切走时存下当前内容，切回时恢复。
const draftKey = computed(() => chat.sessionId || `draft:${chat.draftWorkspaceId || 'none'}`)
let lastDraftKey = draftKey.value
watch(draftKey, (next) => {
  ui.composerDrafts[lastDraftKey] = draft.value
  lastDraftKey = next
  draft.value = ui.composerDrafts[next] ?? ''
  void nextTick(() => resize())
})

watch(
  () => chat.streaming,
  (busy) => {
    if (!busy) void nextTick(() => textarea.value?.focus())
  },
)

watch(
  () => chat.sessionId,
  () => {
    attachments.value = []
    toolsOpen.value = false
    modeOpen.value = false
    chat.restorePermissionMode(workspace.preferences.permission_mode)
  },
)

// 设置里改默认模式时，还没单独选过的会话跟着更新
watch(
  () => workspace.preferences.permission_mode,
  (mode) => {
    if (!chat.sessionId || chat.sessionModes[chat.sessionId] === undefined) {
      chat.restorePermissionMode(mode)
    }
  },
)

// 模型补上之后立刻收起提示，不用等计时器
watch(
  () => hasModel.value,
  (value) => {
    if (value) noModelHint.value = false
  },
)

/** Publish the floating composer's height so the transcript can reserve space. */
function syncComposerHeight(): void {
  const height = formEl.value?.offsetHeight ?? 0
  document.documentElement.style.setProperty('--composer-height', `${Math.round(height)}px`)
  // The blur sheet starts a little above the input shell and follows it when
  // attachments or a request card change the layout.
  const shell = shellEl.value
  const sheetTop = shell ? Math.max(0, shell.offsetTop - 12) : 2
  document.documentElement.style.setProperty('--composer-sheet-top', `${Math.round(sheetTop)}px`)
}

/** Fade the newline hint out before the centred status row can reach it. */
function syncHintCollision(): void {
  const hint = hintEl.value
  const status = statusEl.value
  if (!hint || !status || noModelHint.value) {
    hintSuppressed.value = false
    return
  }
  const gap = 14
  const hintRight = hint.getBoundingClientRect().right
  const statusLeft = status.getBoundingClientRect().left
  hintSuppressed.value = hintRight + gap > statusLeft
}

function syncComposerLayout(): void {
  syncComposerHeight()
  syncHintCollision()
}

watch(
  () => [chat.cacheKnown, chat.turns, chat.steps, chat.contextPercent, noModelHint.value],
  () => { void nextTick(syncHintCollision) },
)

// The form is created only while a workspace is active; keep observing the
// current element so the reserved height follows it.
watch(formEl, (element) => {
  composerObserver?.disconnect()
  if (element && composerObserver) composerObserver.observe(element)
  void nextTick(syncComposerLayout)
})

onMounted(() => {
  document.addEventListener('click', onDocumentClick)
  document.addEventListener('keydown', onDocumentKeydown)
  chat.restorePermissionMode(workspace.preferences.permission_mode)
  resize()
  void nextTick(syncComposerLayout)
  composerObserver = new ResizeObserver(() => syncComposerLayout())
  if (formEl.value) composerObserver.observe(formEl.value)
  window.addEventListener('resize', syncComposerLayout)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', onDocumentClick)
  document.removeEventListener('keydown', onDocumentKeydown)
  window.removeEventListener('resize', syncComposerLayout)
  composerObserver?.disconnect()
  document.documentElement.style.removeProperty('--composer-height')
  if (noModelTimer !== undefined) window.clearTimeout(noModelTimer)
})
</script>

<template>
  <form
    v-if="workspace.activeWorkspace"
    ref="formEl"
    class="composer"
    :class="{ 'composer-request': showRequest }"
    @submit.prevent="send"
  >
    <template v-if="showRequest">
      <ApprovalCard
        v-if="chat.pendingApproval"
        :approval="chat.pendingApproval"
        @decide="decideApproval"
      />
      <QuestionCard
        v-if="chat.pendingQuestion"
        :question="chat.pendingQuestion"
        @answer="answerQuestion"
      />
    </template>
    <template v-else>
    <input
      ref="fileInput"
      type="file"
      class="composer-file-input"
      multiple
      @change="onFiles"
    />
    <div v-if="toolsOpen" ref="toolsMenu" class="composer-tools-menu">
      <div class="composer-tool-section">
        <div class="composer-tool-title">添加</div>
        <button type="button" class="composer-tool-row" @click="chooseFile">
          <span class="composer-tool-name">文件</span>
          <span class="composer-tool-desc">导入文件</span>
        </button>
      </div>
      <div class="composer-tool-section">
        <div class="composer-tool-title">命令</div>
        <button
          type="button"
          class="composer-tool-row"
          :disabled="compacting || chat.streaming || !chat.sessionId"
          @click="runCompact"
        >
          <span class="composer-tool-name">压缩 <code>compact</code></span>
          <span class="composer-tool-desc">{{ compacting ? '正在压缩…' : '压缩以上对话内容' }}</span>
        </button>
      </div>
    </div>
    <div v-if="attachments.length" class="composer-attachments">
      <span v-for="(file, index) in attachments" :key="file.path" class="composer-attachment">
        <span class="composer-attachment-name">{{ file.name }}</span>
        <button
          type="button"
          class="composer-attachment-remove"
          :aria-label="`移除 ${file.name}`"
          @click="removeAttachment(index)"
        >
          ×
        </button>
      </span>
    </div>
    <div class="composer-shell" ref="shellEl">
      <textarea
        id="input"
        ref="textarea"
        v-model="draft"
        rows="1"
        placeholder="输入消息，Enter 发送"
        autocomplete="off"
        :disabled="chat.busy || !workspace.activeWorkspace"
        @input="resize"
        @keydown="onKeydown"
      ></textarea>
      <div class="composer-bar">
        <div class="composer-bar-left">
          <button
            type="button"
            class="composer-plus"
            :class="{ active: toolsOpen }"
            aria-label="添加文件或执行命令"
            :aria-expanded="toolsOpen"
            :disabled="uploading"
            @click.stop="toggleTools"
          >
            <svg v-if="!uploading" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
              <path d="M8 3v10M3 8h10" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
            </svg>
            <span v-else aria-hidden="true">…</span>
          </button>
          <div ref="switcher" class="model-switcher">
            <button
              id="model-switch"
              type="button"
              class="model-switch"
              aria-haspopup="listbox"
              :aria-expanded="menuOpen"
              :title="modelTitle"
              @click.stop="toggleMenu"
            >
              <ProviderIcon
                v-if="activeProvider"
                :provider="activeProvider.provider"
                :size="18"
              />
              <span v-else class="status-dot" aria-hidden="true"></span>
              <span id="model-switch-name">{{ activeModelName }}</span>
              <span class="caret" aria-hidden="true">▴</span>
            </button>

            <div v-if="menuOpen" class="model-menu" role="listbox">
              <p v-if="!workspace.providers.length" class="model-option empty">
                还没有供应商
              </p>
              <template v-else>
                <div class="model-provider-tabs">
                  <button
                    v-for="provider in workspace.providers"
                    :key="provider.id"
                    type="button"
                    class="model-provider-tab"
                    :class="{ active: provider.id === menuProvider?.id }"
                    :title="providerTitle(provider)"
                    @click="switchProvider(provider)"
                  >
                    <ProviderIcon :provider="provider.provider" :size="18" />
                    <span>{{ providerTitle(provider) }}</span>
                  </button>
                </div>

                <div class="model-menu-list">
                  <template v-if="menuProvider">
                    <button
                      v-for="model in menuModels"
                      :key="model.id"
                      type="button"
                      class="model-option"
                      :class="{
                        active: menuProvider.id === workspace.activeProviderId
                          && model.id === workspace.activeModelId,
                      }"
                      role="option"
                      :aria-selected="menuProvider.id === workspace.activeProviderId
                        && model.id === workspace.activeModelId"
                      @click="switchModel(menuProvider, model)"
                    >
                      <span class="model-option-name">{{ model.name || model.model }}</span>
                      <span class="model-option-meta">
                        {{ model.model }} · 思考：{{ thinkingLabel(model.thinking) }}
                      </span>
                    </button>
                    <p v-if="!menuModels.length" class="model-option empty">
                      该供应商还没有模型
                    </p>
                  </template>
                </div>
              </template>

              <button type="button" class="model-option manage" @click="openSettings">
                管理供应商…
              </button>
            </div>
          </div>
          <div v-if="workspace.activeModel?.supports_reasoning !== false" ref="thinkingSwitcher" class="thinking-switcher">
            <button
              type="button"
              class="thinking-switch"
              :disabled="!workspace.activeModel"
              :aria-expanded="thinkingOpen"
              title="调整推理等级"
              @click.stop="toggleThinking"
            >
              <span>推理</span>
              <strong>{{ thinkingShort(currentThinking) }}</strong>
            </button>
            <div v-if="thinkingOpen" class="thinking-menu">
              <input
                class="thinking-range"
                type="range"
                min="0"
                :max="Math.max(0, thinkingLevels.length - 1)"
                step="1"
                :value="thinkingIndex"
                :disabled="thinkingLevels.length < 2"
                @input="onThinkingSlide"
              />
              <div class="thinking-scale">
                <span v-for="level in thinkingLevels" :key="level">{{ thinkingShort(level) }}</span>
              </div>
            </div>
          </div>
        </div>
        <div class="composer-bar-right">
          <div>
            <button
              ref="modeButton"
              type="button"
              class="mode-switch"
              :class="{ active: modeOpen }"
              aria-haspopup="menu"
              :aria-expanded="modeOpen"
              @click.stop="toggleModeMenu"
            >
              <span>{{ currentModeOption.label }}</span>
              <span class="caret" aria-hidden="true">▴</span>
            </button>
            <FloatingMenu
              :open="modeOpen"
              :anchor="modeButton"
              :width="224"
              :height="200"
              placement="top"
              @close="modeOpen = false"
            >
              <button
                v-for="mode in PERMISSION_MODES"
                :key="mode.value"
                type="button"
                class="mode-option"
                :class="{ active: mode.value === chat.permissionMode }"
                role="menuitemradio"
                :aria-checked="mode.value === chat.permissionMode"
                @click="chooseMode(mode.value)"
              >
                <span class="mode-option-name">{{ mode.label }}</span>
                <span class="mode-option-hint">{{ mode.hint }}</span>
              </button>
            </FloatingMenu>
          </div>
          <button
            v-if="chat.streaming"
            type="button"
            class="btn-ghost composer-stop"
            @click="chat.stop()"
          >
            停止
          </button>
          <button
            type="submit"
            class="composer-send"
            :class="{ 'is-inactive': !canSend || !hasModel }"
            :aria-disabled="!canSend || !hasModel"
            aria-label="发送"
            :title="!hasModel
              ? '当前没有可用的模型'
              : canSend ? '发送（Enter）' : '输入内容后可发送'"
          >
            <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
              <path
                d="M8 12.4V3.9M4.2 7.6 8 3.8l3.8 3.8"
                fill="none"
                stroke="currentColor"
                stroke-width="1.7"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
          </button>
        </div>
      </div>
    </div>

    </template>

    <div class="composer-meta">
      <div
        ref="hintEl"
        v-if="!showRequest"
        class="composer-hint"
        :class="{ suppressed: hintSuppressed }"
      >
        <Transition name="hint-fade" mode="out-in">
          <span v-if="noModelHint" key="alert" class="composer-hint-alert" role="status">
            无可用模型，请先在「设置 → 供应商」里添加供应商与模型
          </span>
          <span v-else key="tip">Shift + Enter 换行</span>
        </Transition>
      </div>

      <div ref="statusEl" class="composer-status">
      <div
        v-if="chat.cacheKnown"
        class="cache-stat"
        @mouseenter="cacheOpen = true"
        @mouseleave="cacheOpen = false"
      >
        <button
          type="button"
          class="cache-meter"
          :class="{ warm: chat.cachePercent >= 50 }"
          :aria-expanded="cacheOpen"
          aria-label="缓存命中详情"
          @focus="cacheOpen = true"
          @blur="cacheOpen = false"
        >
          <span class="cache-meter-dot" aria-hidden="true"></span>
          <span>缓存命中 {{ chat.cachePercent }}%</span>
        </button>
        <div v-if="cacheOpen" class="cache-popover" role="status">
          <div class="cache-popover-row">
            <span class="cache-popover-label">会话命中率</span>
            <span class="cache-popover-value">{{ chat.cachePercent }}%</span>
          </div>
          <div class="cache-popover-row">
            <span class="cache-popover-label">缓存读取</span>
            <span class="cache-popover-value">{{ compactTokens(chat.cacheReadTokens) }}</span>
          </div>
          <div class="cache-popover-row">
            <span class="cache-popover-label">提示词</span>
            <span class="cache-popover-value">{{ compactTokens(chat.cachePromptTokens) }}</span>
          </div>
          <div class="cache-popover-row">
            <span class="cache-popover-label">模型请求</span>
            <span class="cache-popover-value">{{ chat.cacheRequests }}</span>
          </div>
          <template v-if="chat.turnCachePercent !== null">
            <div class="cache-popover-rule" aria-hidden="true"></div>
            <div class="cache-popover-row">
              <span class="cache-popover-label">本轮命中率</span>
              <span class="cache-popover-value">{{ chat.turnCachePercent }}%</span>
            </div>
            <div class="cache-popover-row">
              <span class="cache-popover-label">本轮读取</span>
              <span class="cache-popover-value">{{ compactTokens(chat.turnCacheReadTokens) }}</span>
            </div>
            <div class="cache-popover-row">
              <span class="cache-popover-label">本轮提示词</span>
              <span class="cache-popover-value">{{ compactTokens(chat.turnPromptTokens) }}</span>
            </div>
          </template>
        </div>
      </div>
      <span v-if="chat.turns" class="composer-status-item turn-stat">
        {{ chat.turns }} 轮<template v-if="chat.steps"> · {{ chat.steps }} 步</template>
      </span>
      <div class="composer-status-item context-stat" :title="contextTitle">
        <span v-if="chat.contextMax" class="context-tokens">
          {{ compactTokens(chat.contextTokens) }} / {{ compactTokens(chat.contextMax) }}
        </span>
        <span class="context-ring">
          <svg viewBox="0 0 36 36" aria-hidden="true">
            <circle class="context-ring-track" cx="18" cy="18" r="15" pathLength="100" />
            <circle
              class="context-ring-value"
              cx="18"
              cy="18"
              r="15"
              pathLength="100"
              :stroke-dasharray="`${chat.contextPercent} 100`"
            />
          </svg>
          <span class="context-ring-label">{{ chat.contextPercent }}%</span>
        </span>
      </div>
      </div>
    </div>
  </form>
</template>
