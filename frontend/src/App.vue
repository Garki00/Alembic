<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AppSidebar from './components/AppSidebar.vue'
import AppTitlebar from './components/AppTitlebar.vue'
import ChatComposer from './components/ChatComposer.vue'
import ChatTopbar from './components/ChatTopbar.vue'
import ChatTranscript from './components/ChatTranscript.vue'
import GlobalTooltip from './components/GlobalTooltip.vue'
import InspectorSidebar from './components/InspectorSidebar.vue'
import OnboardingModal from './components/OnboardingModal.vue'
import PanelToggle from './components/PanelToggle.vue'
import ProcessPanel from './components/ProcessPanel.vue'
import SettingsModal from './components/SettingsModal.vue'
import SubagentApprovalBar from './components/SubagentApprovalBar.vue'
import SubagentPanel from './components/SubagentPanel.vue'
import ToastHost from './components/ToastHost.vue'
import WorkspaceModal from './components/WorkspaceModal.vue'
import { errorMessage } from './api/client'
import { applyTheme, watchSystemTheme } from './lib/theme'
import { useChatStore } from './stores/chat'
import { INSPECTOR_MIN, useInspectorStore } from './stores/inspector'
import { useProcessStore } from './stores/processes'
import { useSubagentStore } from './stores/subagents'
import { SIDEBAR_MIN, useUiStore } from './stores/ui'
import { useWorkspaceStore } from './stores/workspace'

const workspace = useWorkspaceStore()
const chat = useChatStore()
const ui = useUiStore()
const inspector = useInspectorStore()
const processes = useProcessStore()
const subagents = useSubagentStore()

const bootError = ref('')
const mainEl = ref<HTMLElement | null>(null)
const composerDock = ref<HTMLElement | null>(null)
let dockObserver: ResizeObserver | null = null
let stopSystemThemeWatch: (() => void) | null = null

const MIN_CHAT_WIDTH = 560
let layoutFrame = 0

/** Keep the conversation column readable when both panels are resized. */
function clampLayout(): void {
  const viewport = window.innerWidth || document.documentElement.clientWidth
  if (!viewport) return

  let sidebarWidth = ui.sidebarCollapsed ? 0 : ui.sidebarWidth
  let inspectorWidth = inspector.open ? inspector.width : 0
  let overflow = sidebarWidth + inspectorWidth + MIN_CHAT_WIDTH - viewport
  if (overflow <= 0) return

  // Below this width the subagent page overlays the conversation, so the grid
  // columns do not compete for space.
  if (
    inspector.open
    && inspector.activeTab?.kind === 'subagent'
    && window.matchMedia('(max-width: 1400px)').matches
  ) {
    return
  }

  const inspectorMin = inspector.open ? inspector.activeMinWidth : MIN_CHAT_WIDTH

  if (inspector.open) {
    const target = Math.max(inspectorMin, inspectorWidth - overflow)
    if (target !== inspectorWidth) {
      inspector.setWidth(target)
      overflow -= inspectorWidth - target
      inspectorWidth = target
    }
  }

  if (overflow > 0 && !ui.sidebarCollapsed) {
    const target = Math.max(SIDEBAR_MIN, sidebarWidth - overflow)
    if (target !== sidebarWidth) {
      ui.setSidebarWidth(target)
      overflow -= sidebarWidth - target
      sidebarWidth = target
    }
  }

  if (overflow <= 0) return
  if (inspector.open && inspectorMin >= MIN_CHAT_WIDTH) {
    // A subagent page keeps the main conversation's minimum width; narrowing
    // the window collapses the left sidebar and the inspector overlays instead.
    if (!ui.sidebarCollapsed) ui.sidebarCollapsed = true
    return
  }
  if (inspector.open) {
    inspector.hide()
  } else if (!ui.sidebarCollapsed) {
    ui.sidebarCollapsed = true
  }
}

function queueClamp(): void {
  if (layoutFrame) return
  layoutFrame = window.requestAnimationFrame(() => {
    layoutFrame = 0
    clampLayout()
  })
}

watch(
  () => [ui.sidebarWidth, ui.sidebarCollapsed, inspector.open, inspector.width],
  queueClamp,
  { flush: 'post' },
)

watch(
  () => inspector.activeId,
  () => {
    queueClamp()
    inspector.setWidth(inspector.width)
  },
  { flush: 'post' },
)

watch(
  () => chat.sessionId,
  () => {
    void subagents.refresh()
  },
)

watch(
  () => workspace.ready,
  (ready) => {
    if (ready) queueClamp()
  },
)

// 外观：偏好里的 light / dark / system 直接映射到 <html data-theme>
applyTheme(workspace.preferences?.theme)
watch(
  () => workspace.preferences?.theme,
  (mode) => { applyTheme(mode) },
)

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

    // 只自动打开还能在工作区列表里找到的会话：配置被重置后会残留一些指向已删除
    // 工作区的会话，它们不在侧栏里，绝不能凭空出现在对话区。
    const first = workspace.sessions.find(
      (item) => !item.workspace_id || workspace.workspaces.some((ws) => ws.id === item.workspace_id),
    )
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
  // 跟随系统时，系统切换深浅色要立刻生效
  stopSystemThemeWatch = watchSystemTheme(() => applyTheme(workspace.preferences?.theme))
  // 输入区浮在对话区底部：把它的高度写进 CSS 变量，滚动区据此留出底部空间
  if (composerDock.value && typeof ResizeObserver !== 'undefined') {
    dockObserver = new ResizeObserver(() => {
      const height = composerDock.value?.offsetHeight ?? 0
      mainEl.value?.style.setProperty('--composer-dock-height', `${height}px`)
    })
    dockObserver.observe(composerDock.value)
  }
  window.addEventListener('resize', queueClamp)
  queueClamp()
  processes.init()
  subagents.init()
  void initialize()
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown)
  window.removeEventListener('resize', queueClamp)
  if (layoutFrame) {
    window.cancelAnimationFrame(layoutFrame)
    layoutFrame = 0
  }
  dockObserver?.disconnect()
  dockObserver = null
  stopSystemThemeWatch?.()
  processes.disconnectEvents()
  subagents.disconnectEvents()
})
</script>

<template>
  <div class="desktop-shell">
    <AppTitlebar />
    <div
      class="app"
      :class="{
        'sidebar-open': ui.sidebarOpen,
        'sidebar-collapsed': ui.sidebarCollapsed,
        'inspector-open': inspector.open,
        'subagent-inspector': inspector.activeTab?.kind === 'subagent',
      }"
      :style="{
        '--sidebar-width': `${ui.sidebarWidth}px`,
        '--inspector-width': `${inspector.width}px`,
      }"
    >
    <AppSidebar />
    <PanelToggle
      class="sidebar-toggle"
      side="left"
      :open="!ui.sidebarCollapsed"
      @toggle="ui.toggleSidebarCollapsed()"
    />
    <main ref="mainEl" class="main">
      <ChatTopbar />
      <ProcessPanel v-if="processes.panelOpen" />
      <SubagentPanel v-if="subagents.panelOpen" />
      <ChatTranscript />
      <div ref="composerDock" class="composer-dock">
        <ChatComposer />
      </div>
    </main>
    <Transition name="pane">
      <InspectorSidebar v-if="inspector.open" />
    </Transition>
    </div>
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

  <OnboardingModal v-if="workspace.ready && workspace.firstRun" />
  <SettingsModal v-else-if="ui.settingsOpen" />
  <WorkspaceModal v-if="ui.workspaceModalOpen" />
  <SubagentApprovalBar />
  <GlobalTooltip />
  <ToastHost />
</template>
