<script setup lang="ts">
import { ref } from 'vue'
import type { ToolInfo } from '../api/types'
import { errorMessage } from '../api/client'
import { useChatStore } from '../stores/chat'
import { useUiStore } from '../stores/ui'
import { useWorkspaceStore } from '../stores/workspace'
import ToolCard from './ToolCard.vue'

type ToolView = 'grid' | 'list'

const store = useWorkspaceStore()
const chat = useChatStore()
const ui = useUiStore()

const view = ref<ToolView>('grid')

function signature(tool: ToolInfo): string {
  return `${tool.name}|${tool.fields.map((field) => (
    [field.key, field.value, field.value_set, field.hint].join(':')
  )).join(',')}`
}

function setView(next: ToolView): void {
  view.value = next
}

async function clearApprovals(): Promise<void> {
  try {
    await chat.clearApprovals()
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  }
}
</script>

<template>
  <section class="tab-panel" id="tab-tools">
    <div class="tools-head">
      <p class="muted">开关工具，参数保存后下一轮生效。</p>
      <div class="view-switch" :class="{ list: view === 'list' }" role="group" aria-label="工具视图">
        <button
          type="button"
          class="view-switch-btn"
          :class="{ active: view === 'grid' }"
          :aria-pressed="view === 'grid'"
          title="网格视图"
          @click="setView('grid')"
        >
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <rect x="2.5" y="2.5" width="4.6" height="4.6" rx="1" fill="currentColor" />
            <rect x="8.9" y="2.5" width="4.6" height="4.6" rx="1" fill="currentColor" />
            <rect x="2.5" y="8.9" width="4.6" height="4.6" rx="1" fill="currentColor" />
            <rect x="8.9" y="8.9" width="4.6" height="4.6" rx="1" fill="currentColor" />
          </svg>
        </button>
        <button
          type="button"
          class="view-switch-btn"
          :class="{ active: view === 'list' }"
          :aria-pressed="view === 'list'"
          title="列表视图"
          @click="setView('list')"
        >
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <rect x="2.5" y="3" width="2.6" height="2.6" rx="0.8" fill="currentColor" />
            <rect x="6.7" y="3.4" width="6.8" height="1.8" rx="0.9" fill="currentColor" />
            <rect x="2.5" y="6.7" width="2.6" height="2.6" rx="0.8" fill="currentColor" />
            <rect x="6.7" y="7.1" width="6.8" height="1.8" rx="0.9" fill="currentColor" />
            <rect x="2.5" y="10.4" width="2.6" height="2.6" rx="0.8" fill="currentColor" />
            <rect x="6.7" y="10.8" width="6.8" height="1.8" rx="0.9" fill="currentColor" />
          </svg>
        </button>
      </div>
    </div>

    <div class="tool-list" :class="view">
      <p v-if="!store.tools.length" class="muted">
        没有取到工具列表，请确认服务已重启并刷新页面。
      </p>
      <ToolCard
        v-for="tool in store.tools"
        :key="signature(tool)"
        :tool="tool"
        :view="view"
      />
    </div>

    <div class="tool-foot">
      <button type="button" class="btn-mini danger" @click="clearApprovals">
        清除本会话「始终允许」
      </button>
    </div>
  </section>
</template>
