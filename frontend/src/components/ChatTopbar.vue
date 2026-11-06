<script setup lang="ts">
import { computed } from 'vue'
import { useChatStore } from '../stores/chat'
import { useUiStore } from '../stores/ui'
import { useWorkspaceStore } from '../stores/workspace'

const workspace = useWorkspaceStore()
const chat = useChatStore()
const ui = useUiStore()

const title = computed(() => {
  const group = workspace.sessions.find((item) => item.id === chat.sessionId)
  return [workspace.activeWorkspace?.name, group?.title].filter(Boolean).join(' / ')
})

const path = computed(() => workspace.activeWorkspace?.path || '')
</script>

<template>
  <header class="topbar">
    <button
      type="button"
      class="icon-btn menu-btn"
      aria-label="工作区与对话"
      @click="ui.sidebarOpen = !ui.sidebarOpen"
    >
      ☰
    </button>
    <div class="topbar-title" :title="path">{{ title }}</div>
  </header>
</template>
