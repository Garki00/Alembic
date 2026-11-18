<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'
import type { SessionSummary } from '../api/types'
import { errorMessage } from '../api/client'
import { relativeTime } from '../lib/time'
import { useChatStore, type SessionStatus } from '../stores/chat'
import { useUiStore } from '../stores/ui'
import { useWorkspaceStore } from '../stores/workspace'
import ConfirmDialog from './ConfirmDialog.vue'
import FloatingMenu from './FloatingMenu.vue'

const props = defineProps<{
  session: SessionSummary
  active: boolean
  archived?: boolean
  draggable?: boolean
  dropHint?: 'before' | 'after' | ''
  status?: SessionStatus | null
}>()

const emit = defineEmits<{
  (event: 'select'): void
  (event: 'dragstart', id: string): void
  (event: 'dragover', id: string, before: boolean): void
  (event: 'dragend'): void
  (event: 'drop', id: string): void
}>()

const store = useWorkspaceStore()
const chat = useChatStore()
const ui = useUiStore()

// 侧栏状态：等待审批/选择优先于「运行中」
const statusKind = computed(() => {
  const status = props.status
  if (!status) return ''
  if (status.approval) return 'approval'
  if (status.question) return 'question'
  if (status.running) return 'running'
  return ''
})
const statusTitle = computed(() => {
  switch (statusKind.value) {
    case 'running': return '正在运行'
    case 'approval': return '等待审批'
    case 'question': return '等待选择'
    default: return ''
  }
})

const menuOpen = ref(false)
const confirming = ref(false)
const renaming = ref(false)
const renameValue = ref('')
const renameInput = ref<HTMLInputElement | null>(null)
const moreButton = ref<HTMLButtonElement | null>(null)
const titleWrap = ref<HTMLElement | null>(null)
const titleLabel = ref<HTMLElement | null>(null)
const marquee = ref(0)
let marqueeTimer = 0

const title = computed(() => props.session.title || '新对话')
const timeText = computed(() => relativeTime(props.session.updated_at))
const pinned = computed(() => Boolean(props.session.pinned))
const marqueeStyle = computed(() => (
  marquee.value > 0
    ? {
        animationDuration: `${Math.max(2, marquee.value / 38)}s`,
        '--session-shift': `-${marquee.value}px`,
      }
    : undefined
))

function onEnter(): void {
  window.clearTimeout(marqueeTimer)
  marqueeTimer = window.setTimeout(() => {
    void nextTick(() => {
      const wrap = titleWrap.value
      const label = titleLabel.value
      if (!wrap || !label) return
      marquee.value = Math.max(0, label.scrollWidth - wrap.clientWidth)
    })
  }, 160)
}

function onLeave(): void {
  window.clearTimeout(marqueeTimer)
  marquee.value = 0
}

function onClick(): void {
  if (props.archived || renaming.value || menuOpen.value) return
  emit('select')
}

function onDragStart(event: DragEvent): void {
  if (!props.draggable || props.archived) {
    event.preventDefault()
    return
  }
  emit('dragstart', props.session.id)
  if (event.dataTransfer) {
    event.dataTransfer.setData('text/plain', props.session.id)
    event.dataTransfer.effectAllowed = 'move'
  }
}

function onDragOver(event: DragEvent): void {
  if (!props.draggable || props.archived) return
  event.preventDefault()
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  emit('dragover', props.session.id, event.clientY < rect.top + rect.height / 2)
}

function onDragEnd(): void {
  emit('dragend')
}

function onDrop(event: DragEvent): void {
  if (!props.draggable || props.archived) return
  event.preventDefault()
  emit('drop', props.session.id)
}

async function patch(payload: { title?: string; pinned?: boolean; archived?: boolean }): Promise<void> {
  try {
    await store.updateSession(props.session.id, payload)
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  }
}

async function togglePin(): Promise<void> {
  if (props.archived) return
  menuOpen.value = false
  await patch({ pinned: !pinned.value })
}

function startRename(): void {
  menuOpen.value = false
  renaming.value = true
  renameValue.value = title.value
  void nextTick(() => {
    renameInput.value?.focus()
    renameInput.value?.select()
  })
}

async function finishRename(save: boolean): Promise<void> {
  if (!renaming.value) return
  renaming.value = false
  const value = renameValue.value.trim()
  if (!save || !value || value === title.value) return
  await patch({ title: value })
}

async function branch(): Promise<void> {
  menuOpen.value = false
  try {
    const created = await store.branchSession(props.session.id)
    await store.refreshSessions()
    if (created.workspace_id) {
      store.activeWorkspaceId = created.workspace_id
      ui.expanded[created.workspace_id] = true
    }
    await chat.openSession(created.id)
    ui.sidebarOpen = false
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  }
}

async function archive(): Promise<void> {
  menuOpen.value = false
  await patch({ archived: true })
  ui.archivedOpen = true
  if (chat.sessionId === props.session.id) {
    chat.sessionId = null
    chat.resetConversation()
  }
}

async function unarchive(): Promise<void> {
  menuOpen.value = false
  await patch({ archived: false })
}

function remove(): void {
  menuOpen.value = false
  confirming.value = true
}

async function confirmRemove(): Promise<void> {
  confirming.value = false
  try {
    await store.deleteSession(props.session.id)
    if (chat.sessionId === props.session.id) {
      chat.sessionId = null
      chat.resetConversation()
    }
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  }
}

onBeforeUnmount(() => window.clearTimeout(marqueeTimer))
</script>

<template>
  <div
    class="session-item"
    :class="{
      active,
      archived,
      pinned,
      'drop-before': dropHint === 'before',
      'drop-after': dropHint === 'after',
    }"
    role="button"
    :tabindex="archived ? -1 : 0"
    :aria-current="active ? 'page' : undefined"
    :draggable="draggable && !archived"
    @click="onClick"
    @keydown.enter.prevent="onClick"
    @mouseenter="onEnter"
    @mouseleave="onLeave"
    @dragstart="onDragStart"
    @dragover="onDragOver"
    @dragend="onDragEnd"
    @drop="onDrop"
  >
    <span v-if="statusKind" class="session-status" :class="statusKind" :title="statusTitle">
      <svg v-if="statusKind === 'running'" class="spin" viewBox="0 0 16 16" aria-hidden="true">
        <circle
          cx="8"
          cy="8"
          r="5.2"
          fill="none"
          stroke="currentColor"
          stroke-width="1.6"
          stroke-linecap="round"
          stroke-dasharray="19 14"
        />
      </svg>
      <svg v-else-if="statusKind === 'approval'" viewBox="0 0 16 16" aria-hidden="true">
        <circle cx="8" cy="8" r="5.8" fill="none" stroke="currentColor" stroke-width="1.3" />
        <path d="M8 4.9v3.9" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
        <circle cx="8" cy="11.2" r="0.9" fill="currentColor" />
      </svg>
      <svg v-else viewBox="0 0 16 16" aria-hidden="true">
        <circle cx="8" cy="8" r="5.8" fill="none" stroke="currentColor" stroke-width="1.3" />
        <path
          d="M6.4 6.3a1.7 1.7 0 1 1 2.4 1.6c-.5.2-.7.6-.7 1.1v.3"
          fill="none"
          stroke="currentColor"
          stroke-width="1.3"
          stroke-linecap="round"
        />
        <circle cx="8" cy="11.2" r="0.9" fill="currentColor" />
      </svg>
    </span>

    <input
      v-if="renaming"
      ref="renameInput"
      v-model="renameValue"
      class="session-rename"
      @click.stop
      @keydown.enter.prevent="finishRename(true)"
      @keydown.esc.prevent="finishRename(false)"
      @blur="finishRename(true)"
    />
    <span v-else ref="titleWrap" class="session-title-wrap">
      <span
        ref="titleLabel"
        class="session-title"
        :class="{ marquee: marquee > 0 }"
        :style="marqueeStyle"
      >{{ title }}</span>
    </span>

    <span class="session-time">{{ timeText }}</span>

    <span class="session-actions">
      <button
        v-if="!archived"
        type="button"
        class="session-icon"
        :class="{ on: pinned }"
        :title="pinned ? '取消置顶' : '置顶对话'"
        @click.stop="togglePin"
      >
        <svg viewBox="0 0 16 16" aria-hidden="true">
          <path
            d="M9.6 2.2 13.8 6.4l-1.6.4-2.6 2.6-.3 2.5-1.6 1.6-2.1-2.1-3 3-.6-.6 3-3-2.1-2.1 1.6-1.6 2.5-.3 2.6-2.6.4-1.6Z"
            :fill="pinned ? 'currentColor' : 'none'"
            stroke="currentColor"
            stroke-width="1.1"
            stroke-linejoin="round"
          />
        </svg>
      </button>
      <button
        ref="moreButton"
        type="button"
        class="session-icon"
        title="更多"
        @click.stop="menuOpen = !menuOpen"
      >
        <svg viewBox="0 0 16 16" aria-hidden="true">
          <circle cx="8" cy="3.2" r="1.2" fill="currentColor" />
          <circle cx="8" cy="8" r="1.2" fill="currentColor" />
          <circle cx="8" cy="12.8" r="1.2" fill="currentColor" />
        </svg>
      </button>
    </span>

    <FloatingMenu
      :open="menuOpen"
      :anchor="moreButton"
      :height="archived ? 96 : 196"
      @close="menuOpen = false"
    >
      <template v-if="!archived">
        <button type="button" class="session-menu-item" @click="togglePin">
          <svg class="menu-icon" viewBox="0 0 16 16" aria-hidden="true">
            <path
              d="M9.6 2.2 13.8 6.4l-1.6.4-2.6 2.6-.3 2.5-1.6 1.6-2.1-2.1-3 3-.6-.6 3-3-2.1-2.1 1.6-1.6 2.5-.3 2.6-2.6.4-1.6Z"
              fill="none"
              stroke="currentColor"
              stroke-width="1.1"
              stroke-linejoin="round"
            />
          </svg>
          <span>{{ pinned ? '取消置顶' : '置顶对话' }}</span>
        </button>
        <button type="button" class="session-menu-item" @click="startRename">
          <svg class="menu-icon" viewBox="0 0 16 16" aria-hidden="true">
            <path
              d="M10.4 2.9 13.1 5.6 5.9 12.8 2.9 13.1l.3-3z"
              fill="none"
              stroke="currentColor"
              stroke-width="1.2"
              stroke-linejoin="round"
            />
            <path d="m9.2 4.1 2.7 2.7" fill="none" stroke="currentColor" stroke-width="1.1" />
          </svg>
          <span>重命名</span>
        </button>
        <button type="button" class="session-menu-item" @click="branch">
          <svg class="menu-icon" viewBox="0 0 16 16" aria-hidden="true">
            <circle cx="4.6" cy="3.6" r="1.6" fill="none" stroke="currentColor" stroke-width="1.2" />
            <circle cx="4.6" cy="12.4" r="1.6" fill="none" stroke="currentColor" stroke-width="1.2" />
            <circle cx="11.4" cy="6.4" r="1.6" fill="none" stroke="currentColor" stroke-width="1.2" />
            <path
              d="M4.6 5.2v5.6M11.4 8v0.6c0 1.4-1.1 2.4-2.4 2.4H6.2"
              fill="none"
              stroke="currentColor"
              stroke-width="1.2"
              stroke-linecap="round"
            />
          </svg>
          <span>分支对话</span>
        </button>
        <button type="button" class="session-menu-item" @click="archive">
          <svg class="menu-icon" viewBox="0 0 16 16" aria-hidden="true">
            <rect x="2.4" y="3.2" width="11.2" height="2.6" rx="0.7" fill="none" stroke="currentColor" stroke-width="1.2" />
            <path
              d="M3.4 5.8v6.1c0 .6.5 1.1 1.1 1.1h7c.6 0 1.1-.5 1.1-1.1V5.8"
              fill="none"
              stroke="currentColor"
              stroke-width="1.2"
              stroke-linejoin="round"
            />
            <path d="M6.4 8.2h3.2" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" />
          </svg>
          <span>归档对话</span>
        </button>
        <button type="button" class="session-menu-item danger" @click="remove">
          <svg class="menu-icon" viewBox="0 0 16 16" aria-hidden="true">
            <path d="M3.4 4.6h9.2" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" />
            <path
              d="M5.2 4.6V3.4c0-.5.4-.9.9-.9h3.8c.5 0 .9.4.9.9v1.2M4.6 4.6l.6 8c0 .5.4.8.9.8h4.8c.5 0 .9-.3.9-.8l.6-8"
              fill="none"
              stroke="currentColor"
              stroke-width="1.2"
              stroke-linejoin="round"
            />
            <path d="M6.8 6.8v4.4M9.2 6.8v4.4" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" />
          </svg>
          <span>删除对话</span>
        </button>
      </template>
      <template v-else>
        <button type="button" class="session-menu-item" @click="unarchive">
          <svg class="menu-icon" viewBox="0 0 16 16" aria-hidden="true">
            <rect x="2.4" y="3.2" width="11.2" height="2.6" rx="0.7" fill="none" stroke="currentColor" stroke-width="1.2" />
            <path
              d="M3.4 5.8v6.1c0 .6.5 1.1 1.1 1.1h7c.6 0 1.1-.5 1.1-1.1V5.8"
              fill="none"
              stroke="currentColor"
              stroke-width="1.2"
              stroke-linejoin="round"
            />
            <path d="M8 12V8.2M6.4 9.6 8 8l1.6 1.6" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          <span>取消归档</span>
        </button>
        <button type="button" class="session-menu-item danger" @click="remove">
          <svg class="menu-icon" viewBox="0 0 16 16" aria-hidden="true">
            <path d="M3.4 4.6h9.2" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" />
            <path
              d="M5.2 4.6V3.4c0-.5.4-.9.9-.9h3.8c.5 0 .9.4.9.9v1.2M4.6 4.6l.6 8c0 .5.4.8.9.8h4.8c.5 0 .9-.3.9-.8l.6-8"
              fill="none"
              stroke="currentColor"
              stroke-width="1.2"
              stroke-linejoin="round"
            />
            <path d="M6.8 6.8v4.4M9.2 6.8v4.4" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" />
          </svg>
          <span>删除对话</span>
        </button>
      </template>
    </FloatingMenu>

    <ConfirmDialog
      :open="confirming"
      title="删除对话"
      :message="`删除对话「${title}」？此操作不可恢复。`"
      @cancel="confirming = false"
      @confirm="confirmRemove"
    />
  </div>
</template>
