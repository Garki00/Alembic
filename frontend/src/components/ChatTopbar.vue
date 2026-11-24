<script setup lang="ts">
import { computed } from 'vue'
import PanelToggle from './PanelToggle.vue'
import { useChatStore } from '../stores/chat'
import { useInspectorStore } from '../stores/inspector'
import { useProcessStore } from '../stores/processes'
import { useSubagentStore } from '../stores/subagents'
import { useUiStore } from '../stores/ui'
import { useWorkspaceStore } from '../stores/workspace'

const workspace = useWorkspaceStore()
const chat = useChatStore()
const ui = useUiStore()
const inspector = useInspectorStore()
const processes = useProcessStore()
const subagents = useSubagentStore()

function toggleProcesses(): void {
  const opening = !processes.panelOpen
  if (opening) subagents.closePanel()
  processes.togglePanel()
}

function toggleSubagents(): void {
  const opening = !subagents.panelOpen
  if (opening) processes.closePanel()
  subagents.togglePanel()
}

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
      :class="{ active: processes.panelOpen }"
      @click.stop="toggleProcesses()"
    >
      {{ processes.items.length }}个后台任务
    </button>
    <button
      type="button"
      class="process-entry subagent-entry"
      data-subagent-entry
      :class="{ active: subagents.panelOpen }"
      @click.stop="toggleSubagents()"
    >
      {{ subagents.items.length }}个子代理
    </button>
    <PanelToggle
      class="topbar-inspector"
      side="right"
      :open="inspector.open"
      @toggle="inspector.toggle()"
    />
  </header>
</template>
