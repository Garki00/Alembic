<script setup lang="ts">
import { computed } from 'vue'
import { useChatStore } from '../stores/chat'
import { useInspectorStore } from '../stores/inspector'
import { useProcessStore } from '../stores/processes'
import { useUiStore } from '../stores/ui'
import { useWorkspaceStore } from '../stores/workspace'

const workspace = useWorkspaceStore()
const chat = useChatStore()
const ui = useUiStore()
const inspector = useInspectorStore()
const processes = useProcessStore()

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
    <button
      type="button"
      class="process-entry"
      data-process-entry
      title="查看后台任务"
      :class="{ active: processes.panelOpen }"
      @click.stop="processes.togglePanel()"
    >
      {{ processes.items.length }}个后台任务
    </button>
    <button
      type="button"
      class="icon-btn topbar-inspector"
      :class="{ active: inspector.open }"
      aria-label="侧边栏"
      title="侧边栏"
      @click="inspector.toggle()"
    >
      ▥
    </button>
  </header>
</template>
