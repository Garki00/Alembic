<script setup lang="ts">
import { nextTick, ref } from 'vue'
import type { SessionSummary, WorkspaceInfo } from '../api/types'
import { errorMessage } from '../api/client'
import { useChatStore } from '../stores/chat'
import { useUiStore } from '../stores/ui'
import { useWorkspaceStore } from '../stores/workspace'
import ConfirmDialog from './ConfirmDialog.vue'
import FloatingMenu from './FloatingMenu.vue'
import SessionRow from './SessionRow.vue'

const props = defineProps<{
  workspace: WorkspaceInfo
  sessions: SessionSummary[]
  totalCount: number
  dropId?: string
  dropBefore?: boolean
  /** 这个工作区里是否有一个还没发消息的新对话占位 */
  draft?: boolean
}>()

const emit = defineEmits<{
  (event: 'session-select', session: SessionSummary): void
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
const moreButton = ref<HTMLButtonElement | null>(null)
const renaming = ref(false)
const renameValue = ref('')
const renameInput = ref<HTMLInputElement | null>(null)

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
  if (store.activeWorkspaceId !== props.workspace.id) {
    void store.activateWorkspace(props.workspace.id)
  }
  // 只创建「未发消息的新对话」占位，不落库；发第一条消息时才真正建会话。
  chat.startDraft(props.workspace.id)
  ui.sidebarOpen = false
  ui.focusComposer()
}

function openDraft(): void {
  ui.expanded[props.workspace.id] = true
  if (store.activeWorkspaceId !== props.workspace.id) {
    void store.activateWorkspace(props.workspace.id)
  }
  // 已经在别的对话里时，点占位回到「新对话」视图（不会新建会话）
  chat.startDraft(props.workspace.id)
  ui.focusComposer()
}

function removeWorkspace(): void {
  closeMenu()
  confirming.value = true
}

async function confirmRemove(): Promise<void> {
  confirming.value = false
  try {
    await store.deleteWorkspace(props.workspace.id)
    if (chat.sessionId && !store.sessions.some((item) => item.id === chat.sessionId)) {
      chat.sessionId = null
      chat.resetConversation()
    }
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  }
}

function forwardDragOver(id: string, before: boolean): void {
  emit('dragover', id, before)
}

</script>

<template>
  <div
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
      <button
        ref="moreButton"
        type="button"
        class="ws-action"
        title="更多"
        @click.stop="menuOpen = !menuOpen"
      >
        ⋯
      </button>
    </div>

    <FloatingMenu :open="menuOpen" :anchor="moreButton" :height="96" @close="menuOpen = false">
      <button type="button" class="ws-menu-item" @click="startRename">重命名</button>
      <button type="button" class="ws-menu-item danger" @click="removeWorkspace">删除</button>
    </FloatingMenu>

    <div v-show="expanded()" class="ws-sessions">
      <button
        v-if="draft"
        type="button"
        class="session-item draft"
        :class="{ active: !chat.sessionId }"
        title="新对话（发送第一条消息后才会保存）"
        @click="openDraft"
      >
        <span class="session-draft-dot" aria-hidden="true"></span>
        <span class="session-title">新对话</span>
      </button>
      <p v-if="!sessions.length && !draft" class="ws-session-empty">
        {{ ui.search ? '没有匹配的对话' : '还没有对话' }}
      </p>
      <SessionRow
        v-for="session in sessions"
        :key="session.id"
        :session="session"
        :active="session.id === chat.sessionId"
        :status="chat.statusOf(session.id)"
        draggable
        :drop-hint="dropId === session.id ? (dropBefore ? 'before' : 'after') : ''"
        @select="emit('session-select', session)"
        @dragstart="emit('dragstart', $event)"
        @dragover="forwardDragOver"
        @dragend="emit('dragend')"
        @drop="emit('drop', $event)"
      />
    </div>

    <ConfirmDialog
      :open="confirming"
      title="删除工作区"
      :message="`删除工作区「${workspace.name}」及其中的 ${totalCount} 个对话？此操作不可恢复。`"
      @cancel="confirming = false"
      @confirm="confirmRemove"
    />
  </div>
</template>
