<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import type { SessionSummary, WorkspaceInfo } from '../api/types'
import { errorMessage } from '../api/client'
import { useChatStore } from '../stores/chat'
import { useUiStore } from '../stores/ui'
import { useWorkspaceStore } from '../stores/workspace'

const props = defineProps<{
  workspace: WorkspaceInfo
  sessions: SessionSummary[]
  totalCount: number
}>()

const store = useWorkspaceStore()
const chat = useChatStore()
const ui = useUiStore()

const menuOpen = ref(false)
const renaming = ref(false)
const renameValue = ref('')
const renameInput = ref<HTMLInputElement | null>(null)
const root = ref<HTMLElement | null>(null)

const expanded = () => ui.isExpanded(props.workspace.id)

function onHeadClick(): void {
  if (renaming.value) return
  if (store.activeWorkspaceId !== props.workspace.id) {
    void store.activateWorkspace(props.workspace.id)
  }
  ui.toggleExpanded(props.workspace.id)
}

function closeMenu(): void {
  menuOpen.value = false
}

function startRename(): void {
  closeMenu()
  renaming.value = true
  renameValue.value = props.workspace.name
  void nextTick(() => {
    renameInput.value?.focus()
    renameInput.value?.select()
  })
}

async function finishRename(save: boolean): Promise<void> {
  if (!renaming.value) return
  renaming.value = false
  const value = renameValue.value.trim()
  if (!save || !value || value === props.workspace.name) return
  try {
    await store.renameWorkspace(props.workspace.id, value)
    ui.showToast('已重命名')
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  }
}

async function addChat(): Promise<void> {
  ui.expanded[props.workspace.id] = true
  try {
    await chat.newSession(props.workspace.id)
    ui.sidebarOpen = false
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  }
}

async function removeWorkspace(): Promise<void> {
  closeMenu()
  const suffix = props.totalCount ? `，以及其中的 ${props.totalCount} 个对话` : ''
  if (!window.confirm(`删除工作区「${props.workspace.name}」${suffix}？`)) return
  try {
    const removed = await store.deleteWorkspace(props.workspace.id)
    if (chat.sessionId && !store.sessions.some((item) => item.id === chat.sessionId)) {
      chat.sessionId = null
      chat.resetConversation()
    }
    ui.showToast(removed ? `已删除工作区与 ${removed} 个对话` : '已删除工作区')
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  }
}

async function openSession(session: SessionSummary): Promise<void> {
  const fallback = store.workspaces[0]?.id || ''
  const target = session.workspace_id || fallback
  if (target) {
    ui.expanded[target] = true
    if (store.activeWorkspaceId !== target) void store.activateWorkspace(target)
  }
  try {
    await chat.openSession(session.id)
    ui.sidebarOpen = false
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  }
}

async function removeSession(session: SessionSummary): Promise<void> {
  if (!window.confirm('删除这个对话的记录？')) return
  try {
    await store.deleteSession(session.id)
    if (chat.sessionId === session.id) {
      chat.sessionId = null
      chat.resetConversation()
    }
    ui.showToast('对话已删除')
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  }
}

function onDocumentClick(event: MouseEvent): void {
  if (!menuOpen.value) return
  const target = event.target as Node | null
  if (root.value && target && root.value.contains(target)) return
  menuOpen.value = false
}

onMounted(() => document.addEventListener('click', onDocumentClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocumentClick))
</script>

<template>
  <div
    ref="root"
    class="ws-group"
    :class="{ active: workspace.id === store.activeWorkspaceId }"
    :data-id="workspace.id"
  >
    <div class="ws-head" @click="onHeadClick">
      <span class="ws-caret">{{ expanded() ? '▾' : '▸' }}</span>

      <input
        v-if="renaming"
        ref="renameInput"
        v-model="renameValue"
        class="ws-rename"
        @click.stop
        @keydown.enter.prevent="finishRename(true)"
        @keydown.esc.prevent="finishRename(false)"
        @blur="finishRename(true)"
      />
      <span v-else class="ws-name" :title="workspace.path">{{ workspace.name }}</span>

      <span class="ws-count">{{ totalCount || '' }}</span>
      <button
        type="button"
        class="ws-action"
        title="在此工作区新建对话"
        @click.stop="addChat"
      >
        ＋
      </button>
      <button type="button" class="ws-action" title="更多" @click.stop="menuOpen = !menuOpen">
        ⋯
      </button>
    </div>

    <div v-if="menuOpen" class="ws-menu">
      <button type="button" class="ws-menu-item" @click.stop="startRename">重命名</button>
      <button type="button" class="ws-menu-item danger" @click.stop="removeWorkspace">删除</button>
    </div>

    <div v-show="expanded()" class="ws-sessions">
      <p v-if="!sessions.length" class="ws-session-empty">
        {{ ui.search ? '没有匹配的对话' : '还没有对话' }}
      </p>
      <button
        v-for="session in sessions"
        :key="session.id"
        type="button"
        class="session-item"
        :class="{ active: session.id === chat.sessionId }"
        :data-id="session.id"
        @click="openSession(session)"
      >
        <span class="session-main">
          <span class="session-title">{{ session.title || '新对话' }}</span>
          <span class="session-meta">{{ session.turns || 0 }} 轮 · {{ session.messages || 0 }} 条</span>
        </span>
        <span
          class="session-del"
          title="删除对话"
          @click.stop="removeSession(session)"
        >
          ✕
        </span>
      </button>
    </div>
  </div>
</template>
