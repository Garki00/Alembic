<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import AppSidebar from './components/AppSidebar.vue'
import ChatComposer from './components/ChatComposer.vue'
import ChatTopbar from './components/ChatTopbar.vue'
import ChatTranscript from './components/ChatTranscript.vue'
import GlobalTooltip from './components/GlobalTooltip.vue'
import InspectorSidebar from './components/InspectorSidebar.vue'
import OnboardingModal from './components/OnboardingModal.vue'
import ProcessPanel from './components/ProcessPanel.vue'
import SettingsModal from './components/SettingsModal.vue'
import ToastHost from './components/ToastHost.vue'
import WorkspaceModal from './components/WorkspaceModal.vue'
import { errorMessage } from './api/client'
import { useChatStore } from './stores/chat'
import { useInspectorStore } from './stores/inspector'
import { useProcessStore } from './stores/processes'
import { useUiStore } from './stores/ui'
import { useWorkspaceStore } from './stores/workspace'

const workspace = useWorkspaceStore()
const chat = useChatStore()
const ui = useUiStore()
const inspector = useInspectorStore()
const processes = useProcessStore()

const bootError = ref('')

function applyDeepLink(): void {
  const hash = window.location.hash.replace('#', '')
  if (hash === 'settings' || hash === 'settings-models' || hash === 'settings-add') {
    ui.openSettings('models')
  } else if (hash === 'settings-tools') {
    ui.openSettings('tools')
  } else if (hash === 'workspace') {
    ui.openWorkspaceModal()
  } else if (hash === 'welcome') {
    chat.sessionId = null
    chat.resetConversation()
  }
}

async function initialize(): Promise<void> {
  bootError.value = ''
  try {
    await workspace.bootstrap()

    if (!workspace.workspaces.length) {
      chat.sessionId = null
      chat.resetConversation()
      applyDeepLink()
      return
    }

    const first = workspace.sessions[0]
    if (first) {
      const target = first.workspace_id || workspace.workspaces[0]?.id || ''
      if (target) {
        workspace.activeWorkspaceId = target
        ui.expanded[target] = true
      }
      await chat.openSession(first.id)
    } else {
      chat.sessionId = null
      chat.resetConversation()
    }

    applyDeepLink()
  } catch (error) {
    bootError.value = `初始化失败：${errorMessage(error)}`
    ui.showToast(bootError.value, true)
  }
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Escape') return
  if (processes.panelOpen) {
    processes.closePanel()
    return
  }
  if (ui.settingsOpen || ui.workspaceModalOpen || ui.sidebarOpen) {
    ui.closeOverlays()
  }
}

onMounted(() => {
  document.addEventListener('keydown', onKeydown)
  processes.init()
  void initialize()
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown)
  processes.disconnectEvents()
})
</script>

<template>
  <div
    class="app"
    :class="{ 'sidebar-open': ui.sidebarOpen, 'inspector-open': inspector.open }"
    :style="{
      '--sidebar-width': `${ui.sidebarWidth}px`,
      '--inspector-width': `${inspector.width}px`,
    }"
  >
    <AppSidebar />
    <main class="main">
      <ChatTopbar />
      <ProcessPanel v-if="processes.panelOpen" />
      <ChatTranscript />
      <ChatComposer />
    </main>
    <InspectorSidebar v-if="inspector.open" />
  </div>

  <div
    v-if="ui.sidebarOpen"
    class="sidebar-backdrop"
    aria-hidden="true"
    @click="ui.sidebarOpen = false"
  ></div>

  <div v-if="bootError" class="boot-error">
    <span>{{ bootError }}</span>
    <button type="button" class="btn-mini" @click="initialize">重试</button>
  </div>

  <OnboardingModal v-if="workspace.ready && workspace.needsSetup" />
  <SettingsModal v-else-if="ui.settingsOpen" />
  <WorkspaceModal v-if="ui.workspaceModalOpen" />
  <GlobalTooltip />
  <ToastHost />
</template>
