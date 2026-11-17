<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'
import type { SessionSummary } from '../api/types'
import { errorMessage } from '../api/client'
import { relativeTime } from '../lib/time'
import { useChatStore } from '../stores/chat'
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
          {{ pinned ? '取消置顶' : '置顶对话' }}
        </button>
        <button type="button" class="session-menu-item" @click="startRename">重命名</button>
        <button type="button" class="session-menu-item" @click="branch">分支对话</button>
        <button type="button" class="session-menu-item" @click="archive">归档对话</button>
        <button type="button" class="session-menu-item danger" @click="remove">删除对话</button>
      </template>
      <template v-else>
        <button type="button" class="session-menu-item" @click="unarchive">取消归档</button>
        <button type="button" class="session-menu-item danger" @click="remove">删除对话</button>
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
