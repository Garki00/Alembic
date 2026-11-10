<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type {
  ApprovalDecision,
  ProviderInfo,
  ProviderModelInfo,
  ThinkingLevel,
} from '../api/types'
import { errorMessage } from '../api/client'
import { providerName } from '../lib/providers'
import {
  normalizeThinkingLevels,
  thinkingLabel,
  thinkingShort,
} from '../lib/thinking'
import { useChatStore } from '../stores/chat'
import { useUiStore } from '../stores/ui'
import { useWorkspaceStore } from '../stores/workspace'
import ApprovalCard from './ApprovalCard.vue'
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

const activeProvider = computed(() => workspace.activeProvider)
const activeModelName = computed(
  () => workspace.activeModel?.name || workspace.activeModel?.model || '未配置模型',
)
const menuProvider = computed(
  () => workspace.providers.find((item) => item.id === menuProviderId.value)
    ?? workspace.activeProvider,
)
const menuModels = computed(() => menuProvider.value?.models ?? [])

const thinkingState = computed(() => normalizeThinkingLevels(
  workspace.activeModel?.thinking_levels,
  workspace.activeModel?.thinking,
))
const thinkingLevels = computed(() => thinkingState.value.levels)
const currentThinking = computed(() => thinkingState.value.thinking)
const thinkingIndex = computed(() => Math.max(
  0,
  thinkingLevels.value.indexOf(currentThinking.value),
))

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
  const text = draft.value
  if (!text.trim()) return
  draft.value = ''
  void nextTick(() => resize())
  void chat.send(text, workspace.activeWorkspaceId || undefined)
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    send()
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
  if (menuOpen.value) {
    menuProviderId.value = workspace.activeProviderId || workspace.providers[0]?.id || ''
  }
}

function toggleThinking(): void {
  thinkingOpen.value = !thinkingOpen.value
  menuOpen.value = false
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
}

function onDocumentKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    menuOpen.value = false
    thinkingOpen.value = false
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

watch(
  () => chat.streaming,
  (busy) => {
    if (!busy) void nextTick(() => textarea.value?.focus())
  },
)

onMounted(() => {
  document.addEventListener('click', onDocumentClick)
  document.addEventListener('keydown', onDocumentKeydown)
  resize()
})

onBeforeUnmount(() => {
  document.removeEventListener('click', onDocumentClick)
  document.removeEventListener('keydown', onDocumentKeydown)
})
</script>

<template>
  <div
    v-if="workspace.activeWorkspace && (chat.pendingApproval || chat.pendingQuestion)"
    class="composer composer-request"
  >
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
  </div>
  <form v-else-if="workspace.activeWorkspace" class="composer" @submit.prevent="send">
    <textarea
      id="input"
      ref="textarea"
      v-model="draft"
      rows="1"
      placeholder="输入消息，Enter 发送"
      autocomplete="off"
      :disabled="chat.streaming || !workspace.activeWorkspace"
      @input="resize"
      @keydown="onKeydown"
    ></textarea>
    <div class="composer-row">
      <div class="hint">Shift + Enter 换行</div>
      <div class="composer-actions">
        <span class="context-meter">{{ chat.contextMeter }}</span>
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
        <div ref="thinkingSwitcher" class="thinking-switcher">
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
        <button v-if="chat.streaming" type="button" class="btn-ghost" @click="chat.stop()">
          停止
        </button>
        <button
          type="submit"
          class="btn-send"
          :disabled="chat.streaming || !draft.trim() || !workspace.activeWorkspace"
        >
          发送 <span aria-hidden="true">↑</span>
        </button>
      </div>
    </div>
  </form>
</template>
