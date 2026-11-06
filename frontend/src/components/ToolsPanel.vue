<script setup lang="ts">
import type { ToolInfo } from '../api/types'
import { errorMessage } from '../api/client'
import { useChatStore } from '../stores/chat'
import { useUiStore } from '../stores/ui'
import { useWorkspaceStore } from '../stores/workspace'
import ToolCard from './ToolCard.vue'

const store = useWorkspaceStore()
const chat = useChatStore()
const ui = useUiStore()

function signature(tool: ToolInfo): string {
  return `${tool.name}|${tool.fields.map((field) => (
    [field.key, field.value, field.value_set, field.hint].join(':')
  )).join(',')}`
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
    <p class="muted">启用或禁用工具；参数保存后下一轮生效。</p>

    <div class="tool-list">
      <p v-if="!store.tools.length" class="muted">
        没有取到工具列表，请确认服务已重启并刷新页面。
      </p>
      <ToolCard
        v-for="tool in store.tools"
        :key="signature(tool)"
        :tool="tool"
      />
    </div>

    <div class="tool-foot">
      <button type="button" class="btn-mini danger" @click="clearApprovals">
        清除本会话「始终允许」
      </button>
    </div>
  </section>
</template>
