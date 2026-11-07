<script setup lang="ts">
import { computed } from 'vue'
import type { SessionSummary, WorkspaceInfo } from '../api/types'
import WorkspaceGroup from './WorkspaceGroup.vue'
import SunIcon from './SunIcon.vue'
import { useUiStore } from '../stores/ui'
import { useWorkspaceStore } from '../stores/workspace'

const ui = useUiStore()
const workspace = useWorkspaceStore()

interface WorkspaceGroupView {
  workspace: WorkspaceInfo
  sessions: SessionSummary[]
  total: number
}

const groups = computed<WorkspaceGroupView[]>(() => {
  const query = ui.search.trim().toLowerCase()
  const result: WorkspaceGroupView[] = []

  for (const item of workspace.workspaces) {
    const all = workspace.sessionsOf(item.id)
    const workspaceHit = !query
      || item.name.toLowerCase().includes(query)
      || item.path.toLowerCase().includes(query)
    const hits = query
      ? all.filter((session) => (session.title || '').toLowerCase().includes(query))
      : all
    if (!workspaceHit && !hits.length) continue
    result.push({
      workspace: item,
      sessions: workspaceHit ? all : hits,
      total: all.length,
    })
  }
  return result
})
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
      <div class="ws-toolbar">
        <input
          v-model="ui.search"
          class="ws-search"
          type="search"
          placeholder="搜索工作区或对话"
          autocomplete="off"
          aria-label="搜索工作区或对话"
        />
        <button
          type="button"
          class="icon-btn"
          title="添加工作区"
          aria-label="添加工作区"
          @click="ui.openWorkspaceModal()"
        >
          ＋
        </button>
      </div>

      <nav class="workspace-list" aria-label="工作区与对话">
        <p v-if="!workspace.workspaces.length" class="muted ws-empty">
          还没有工作区，点上方 ＋ 添加目录。
        </p>
        <template v-else>
          <WorkspaceGroup
            v-for="group in groups"
            :key="group.workspace.id"
            :workspace="group.workspace"
            :sessions="group.sessions"
            :total-count="group.total"
          />
          <p v-if="!groups.length" class="muted ws-empty">没有匹配的工作区</p>
        </template>
      </nav>
    </div>
  </aside>
</template>
