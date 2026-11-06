<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { ModelInfo } from '../api/types'
import { errorMessage } from '../api/client'
import { useChatStore } from '../stores/chat'
import { useUiStore } from '../stores/ui'
import { useWorkspaceStore } from '../stores/workspace'

const chat = useChatStore()
const workspace = useWorkspaceStore()
const ui = useUiStore()

const draft = ref('')
const textarea = ref<HTMLTextAreaElement | null>(null)
const switcher = ref<HTMLElement | null>(null)
const menuOpen = ref(false)

const activeModelName = computed(() => workspace.activeModelInfo?.name || '未配置模型')
const modelTitle = computed(() => {
  const model = workspace.activeModelInfo
  return model ? `${model.provider} · ${model.model}` : '在设置里添加模型'
})

function resize(): void {
  const element = textarea.value
  if (!element) return
  element.style.height = 'auto'
  element.style.height = `${Math.min(element.scrollHeight, 180)}px`
}

function send(): void {
  if (chat.streaming) return
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

async function switchModel(model: ModelInfo): Promise<void> {
  menuOpen.value = false
  if (model.id === workspace.activeModelId) return
  try {
    await workspace.activateModel(model.id)
    ui.showToast(`已切换到 ${model.name}`)
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  }
}

function openSettings(): void {
  menuOpen.value = false
  ui.openSettings('models')
}

function onDocumentClick(event: MouseEvent): void {
  if (!menuOpen.value) return
  const target = event.target as Node | null
  if (switcher.value && target && switcher.value.contains(target)) return
  menuOpen.value = false
}

function onDocumentKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') menuOpen.value = false
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
  <form class="composer" @submit.prevent="send">
    <textarea
      id="input"
      ref="textarea"
      v-model="draft"
      rows="1"
      placeholder="输入消息，Enter 发送"
      autocomplete="off"
      :disabled="chat.streaming"
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
            @click.stop="menuOpen = !menuOpen"
          >
            <span class="status-dot" aria-hidden="true"></span>
            <span id="model-switch-name">{{ activeModelName }}</span>
            <span class="caret" aria-hidden="true">▴</span>
          </button>
          <div v-if="menuOpen" class="model-menu" role="listbox">
            <div v-if="!workspace.models.length" class="model-option empty">还没有模型</div>
            <button
              v-for="model in workspace.models"
              :key="model.id"
              type="button"
              class="model-option"
              :class="{ active: model.id === workspace.activeModelId }"
              role="option"
              :aria-selected="model.id === workspace.activeModelId"
              @click="switchModel(model)"
            >
              <span class="model-option-name">{{ model.name }}</span>
              <span class="model-option-meta">{{ model.model }}</span>
            </button>
            <button type="button" class="model-option manage" @click="openSettings">
              管理模型…
            </button>
          </div>
        </div>
        <button v-if="chat.streaming" type="button" class="btn-ghost" @click="chat.stop()">
          停止
        </button>
        <button
          type="submit"
          class="btn-send"
          :disabled="chat.streaming || !draft.trim()"
        >
          发送 <span aria-hidden="true">↑</span>
        </button>
      </div>
    </div>
  </form>
</template>
