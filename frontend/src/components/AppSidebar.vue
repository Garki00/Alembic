<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import type { SessionSummary, WorkspaceInfo } from '../api/types'
import { errorMessage } from '../api/client'
import { beginResize } from '../lib/resize'
import { useChatStore } from '../stores/chat'
import { useUiStore } from '../stores/ui'
import { useWorkspaceStore } from '../stores/workspace'
import SessionRow from './SessionRow.vue'
import WorkspaceGroup from './WorkspaceGroup.vue'
import SunIcon from './SunIcon.vue'

const ui = useUiStore()
const workspace = useWorkspaceStore()
const chat = useChatStore()

const viewMenuOpen = ref(false)
const searchInput = ref<HTMLInputElement | null>(null)
const dragId = ref('')
const dragOverId = ref('')
const dragOverBefore = ref(true)
const toolbar = ref<HTMLElement | null>(null)

function onResize(event: PointerEvent): void {
  beginResize('left', event, (width) => ui.setSidebarWidth(width))
}

function byTime(a: SessionSummary, b: SessionSummary): number {
  return String(b.updated_at || '').localeCompare(String(a.updated_at || ''))
}

function ordered(list: SessionSummary[]): SessionSummary[] {
  const pinned = list.filter((item) => item.pinned).sort(byTime)
  const rest = list.filter((item) => !item.pinned)
  if (ui.sessionSort === 'manual' && ui.manualOrder.length) {
    const index = new Map(ui.manualOrder.map((id, position) => [id, position]))
    rest.sort((a, b) => {
      const left = index.get(a.id) ?? Number.MAX_SAFE_INTEGER
      const right = index.get(b.id) ?? Number.MAX_SAFE_INTEGER
      return left === right ? byTime(a, b) : left - right
    })
  } else {
    rest.sort(byTime)
  }
  return [...pinned, ...rest]
}

const query = computed(() => ui.search.trim().toLowerCase())
const archivedSessions = computed(() => ordered(
  workspace.sessions.filter((item) => item.archived),
))

function matchesWorkspace(item: WorkspaceInfo): boolean {
  return !query.value
    || item.name.toLowerCase().includes(query.value)
    || item.path.toLowerCase().includes(query.value)
}

interface WorkspaceGroupView {
  workspace: WorkspaceInfo
  sessions: SessionSummary[]
  total: number
}

const groups = computed<WorkspaceGroupView[]>(() => {
  const result: WorkspaceGroupView[] = []
  for (const item of workspace.workspaces) {
    const all = workspace.sessionsOf(item.id).filter((session) => !session.archived)
    const workspaceHit = matchesWorkspace(item)
    const hits = query.value
      ? all.filter((session) => (session.title || '').toLowerCase().includes(query.value))
      : all
    if (!workspaceHit && !hits.length) continue
    result.push({
      workspace: item,
      sessions: ordered(workspaceHit ? all : hits),
      total: all.length,
    })
  }
  result.sort((a, b) => {
    const latest = (sessions: SessionSummary[]): string => sessions.reduce(
      (value, session) => (String(session.updated_at || '') > value ? String(session.updated_at) : value),
      '',
    )
    return latest(b.sessions).localeCompare(latest(a.sessions))
  })
  return result
})

const flatSessions = computed(() => {
  const workspaceNames = new Map(workspace.workspaces.map((item) => [item.id, item]))
  const list = workspace.sessions.filter((session) => !session.archived).filter((session) => {
    if (!query.value) return true
    if ((session.title || '').toLowerCase().includes(query.value)) return true
    const owner = workspaceNames.get(session.workspace_id)
    return Boolean(owner && (
      owner.name.toLowerCase().includes(query.value)
      || owner.path.toLowerCase().includes(query.value)
    ))
  })
  return ordered(list)
})

const isEmpty = computed(() => (
  ui.sessionGrouping === 'flat'
    ? !flatSessions.value.length
    : !groups.value.length
))

function openSession(session: SessionSummary): void {
  const fallback = workspace.workspaces[0]?.id || ''
  const target = session.workspace_id || fallback
  if (target) {
    ui.expanded[target] = true
    if (workspace.activeWorkspaceId !== target) void workspace.activateWorkspace(target)
  }
  void chat.openSession(session.id)
    .then(() => { ui.sidebarOpen = false })
    .catch((error) => { ui.showToast(errorMessage(error), true) })
}

/** 在当前选中的工作区开一个还没落库的「新对话」占位。 */
function newChat(): void {
  const target = workspace.activeWorkspace?.id || ''
  if (!target) {
    ui.openWorkspaceModal()
    return
  }
  ui.expanded[target] = true
  chat.startDraft(target)
  ui.sidebarOpen = false
  ui.focusComposer()
}

function openDraft(): void {
  const target = chat.draftWorkspaceId
  if (target) {
    ui.expanded[target] = true
    if (workspace.activeWorkspaceId !== target) void workspace.activateWorkspace(target)
    chat.startDraft(target)
  }
  ui.focusComposer()
}

function isDraftWorkspace(workspaceId: string): boolean {
  return chat.draftWorkspaceId === workspaceId
}

function openSearch(): void {
  ui.searchOpen = true
  void nextTick(() => searchInput.value?.focus())
}

function closeSearch(): void {
  ui.searchOpen = false
  ui.search = ''
}

function chooseGrouping(grouping: 'workspace' | 'flat'): void {
  ui.setSessionGrouping(grouping)
  viewMenuOpen.value = false
}

function chooseSort(sort: 'recent' | 'manual'): void {
  ui.setSessionSort(sort)
  viewMenuOpen.value = false
}

function displayOrderIds(): string[] {
  if (ui.sessionGrouping === 'flat') return flatSessions.value.map((item) => item.id)
  return groups.value.flatMap((group) => group.sessions.map((item) => item.id))
}

function onDragStart(id: string): void {
  dragId.value = id
  dragOverId.value = ''
}

function onDragOver(id: string, before: boolean): void {
  dragOverId.value = id
  dragOverBefore.value = before
}

function onDragEnd(): void {
  dragId.value = ''
  dragOverId.value = ''
}

function onDrop(targetId: string, section: SessionSummary[]): void {
  const source = dragId.value
  dragId.value = ''
  dragOverId.value = ''
  if (!source || source === targetId) return
  const listed = section.filter((item) => !item.pinned).map((item) => item.id)
  if (!listed.includes(source) || !listed.includes(targetId)) return
  const moved = listed.filter((id) => id !== source)
  const targetIndex = moved.indexOf(targetId)
  if (targetIndex < 0) return
  moved.splice(dragOverBefore.value ? targetIndex : targetIndex + 1, 0, source)
  const base = ui.manualOrder.length ? [...ui.manualOrder] : displayOrderIds()
  const firstIndex = base.findIndex((id) => listed.includes(id))
  const rest = base.filter((id) => !listed.includes(id))
  const insertAt = firstIndex < 0 ? rest.length : Math.min(firstIndex, rest.length)
  rest.splice(insertAt, 0, ...moved)
  ui.setManualOrder(rest)
}

function onDocumentClick(event: MouseEvent): void {
  if (!viewMenuOpen.value) return
  if (toolbar.value && event.target instanceof Node && toolbar.value.contains(event.target)) return
  viewMenuOpen.value = false
}

onMounted(() => document.addEventListener('click', onDocumentClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocumentClick))
</script>

<template>
  <aside class="sidebar" id="sidebar">
    <div class="brand">
      <SunIcon />
      <span class="brand-text">ALEMBIC</span>
      <button
        type="button"
        class="icon-btn brand-action"
        title="设置"
        aria-label="设置"
        @click="ui.openSettings('models')"
      >
        ⚙
      </button>
    </div>

    <div class="sidebar-inner">
      <button
        type="button"
        class="new-chat-btn"
        title="在当前工作区新建对话"
        @click="newChat"
      >
        <svg viewBox="0 0 16 16" aria-hidden="true">
          <path
            d="M13.4 7.6a5.4 5.4 0 0 1-5.4 5.4H4.6L2.2 14.2l.6-2.4A5.4 5.4 0 1 1 13.4 7.6Z"
            fill="none"
            stroke="currentColor"
            stroke-width="1.25"
            stroke-linejoin="round"
          />
          <path d="M8 5.6v4.2M5.9 7.7h4.2" fill="none" stroke="currentColor" stroke-width="1.25" stroke-linecap="round" />
        </svg>
        <span>新对话</span>
      </button>

      <div ref="toolbar" class="ws-toolbar">
        <Transition name="ws-search" mode="out-in">
          <div v-if="ui.searchOpen" key="search" class="ws-search-box">
            <input
              ref="searchInput"
              v-model="ui.search"
              class="ws-search"
              type="search"
              placeholder="搜索工作区或对话"
              autocomplete="off"
              aria-label="搜索工作区或对话"
              @keydown.esc.prevent="closeSearch"
            />
            <button type="button" class="ws-search-close" title="关闭搜索" @click="closeSearch">
              ×
            </button>
          </div>
          <div v-else key="tools" class="ws-tools">
          <button
            type="button"
            class="icon-btn ws-tool-btn"
            title="视图选项"
            aria-label="视图选项"
            @click.stop="viewMenuOpen = !viewMenuOpen"
          >
            <svg viewBox="0 0 16 16" aria-hidden="true">
              <path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" />
              <circle cx="5.5" cy="4.5" r="1.5" fill="none" stroke="currentColor" stroke-width="1.2" />
              <circle cx="10.5" cy="8" r="1.5" fill="none" stroke="currentColor" stroke-width="1.2" />
              <circle cx="7" cy="11.5" r="1.5" fill="none" stroke="currentColor" stroke-width="1.2" />
            </svg>
          </button>
          <button
            type="button"
            class="icon-btn ws-tool-btn"
            title="搜索"
            aria-label="搜索"
            @click="openSearch"
          >
            <svg viewBox="0 0 16 16" aria-hidden="true">
              <circle cx="7" cy="7" r="4" fill="none" stroke="currentColor" stroke-width="1.4" />
              <path d="m10 10 3.4 3.4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
            </svg>
          </button>
          <button
            type="button"
            class="icon-btn ws-tool-btn"
            title="添加工作区"
            aria-label="添加工作区"
            @click="ui.openWorkspaceModal()"
          >
            <svg viewBox="0 0 16 16" aria-hidden="true">
              <path d="M8 3v10M3 8h10" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
            </svg>
          </button>
          </div>
        </Transition>

        <div v-if="viewMenuOpen" class="ws-menu view-menu" @click.stop>
          <div class="ws-menu-label">分组方式</div>
          <button
            type="button"
            class="ws-menu-item"
            :class="{ active: ui.sessionGrouping === 'workspace' }"
            @click="chooseGrouping('workspace')"
          >
            按工作区
          </button>
          <button
            type="button"
            class="ws-menu-item"
            :class="{ active: ui.sessionGrouping === 'flat' }"
            @click="chooseGrouping('flat')"
          >
            单列表
          </button>
          <div class="ws-menu-divider"></div>
          <div class="ws-menu-label">排序方式</div>
          <button
            type="button"
            class="ws-menu-item"
            :class="{ active: ui.sessionSort === 'recent' }"
            @click="chooseSort('recent')"
          >
            最近更新
          </button>
          <button
            type="button"
            class="ws-menu-item"
            :class="{ active: ui.sessionSort === 'manual' }"
            @click="chooseSort('manual')"
          >
            手动排序
          </button>
        </div>
      </div>

      <nav class="workspace-list" aria-label="工作区与对话">
        <p v-if="isEmpty && !archivedSessions.length" class="muted ws-empty">
          {{ query ? '没有匹配的对话' : (workspace.workspaces.length ? '还没有对话' : '还没有工作区') }}
        </p>

        <template v-if="ui.sessionGrouping === 'flat'">
          <div class="flat-list">
            <button
              v-if="chat.draftWorkspaceId"
              type="button"
              class="session-item draft"
              :class="{ active: !chat.sessionId }"
              title="新对话（发送第一条消息后才会保存）"
              @click="openDraft"
            >
              <span class="session-draft-dot" aria-hidden="true"></span>
              <span class="session-title">新对话</span>
            </button>
            <SessionRow
              v-for="session in flatSessions"
              :key="session.id"
              :session="session"
              :active="session.id === chat.sessionId"
              :status="chat.statusOf(session.id)"
              draggable
              :drop-hint="dragOverId === session.id ? (dragOverBefore ? 'before' : 'after') : ''"
              @select="openSession(session)"
              @dragstart="onDragStart"
              @dragover="onDragOver"
              @dragend="onDragEnd"
              @drop="onDrop($event, flatSessions)"
            />
          </div>
        </template>
        <template v-else>
          <WorkspaceGroup
            v-for="group in groups"
            :key="group.workspace.id"
            :workspace="group.workspace"
            :sessions="group.sessions"
            :total-count="group.total"
            :draft="isDraftWorkspace(group.workspace.id)"
            :drop-id="dragOverId"
            :drop-before="dragOverBefore"
            @session-select="openSession"
            @dragstart="onDragStart"
            @dragover="onDragOver"
            @dragend="onDragEnd"
            @drop="onDrop($event, group.sessions)"
          />
        </template>

        <div v-if="archivedSessions.length" class="ws-group archived-group">
          <div class="ws-head" @click="ui.archivedOpen = !ui.archivedOpen">
            <span class="ws-caret">{{ ui.archivedOpen ? '▾' : '▸' }}</span>
            <span class="ws-name">已归档</span>
            <span class="ws-count">{{ archivedSessions.length }}</span>
          </div>
          <div v-show="ui.archivedOpen" class="ws-sessions">
            <SessionRow
              v-for="session in archivedSessions"
              :key="session.id"
              :session="session"
              :active="session.id === chat.sessionId"
              :status="chat.statusOf(session.id)"
              archived
            />
          </div>
        </div>
      </nav>
    </div>
    <div
      class="sidebar-resizer"
      role="separator"
      aria-orientation="vertical"
      aria-label="调整左侧栏宽度"
      title="拖动调整左侧栏宽度"
      @pointerdown="onResize"
    ></div>
  </aside>
</template>
