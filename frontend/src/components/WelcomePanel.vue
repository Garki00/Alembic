<script setup lang="ts">
import { computed } from 'vue'
import SunIcon from './SunIcon.vue'
import { useUiStore } from '../stores/ui'
import { useWorkspaceStore } from '../stores/workspace'

const workspace = useWorkspaceStore()
const ui = useUiStore()

const hint = computed(() => workspace.activeWorkspace
  ? `工作目录：${workspace.activeWorkspace.path}`
  : '先在左侧添加一个工作区。')

const suggestions = [
  '北京现在几点？',
  '上海今天天气怎么样？',
  '纽约和东京现在差几个小时？',
]

function pick(text: string): void {
  ui.setComposerDraft(text)
}
</script>

<template>
  <div class="welcome">
    <div class="welcome-sun"><SunIcon /></div>
    <div class="eyebrow">{{ workspace.activeWorkspace?.name || '工作区' }}</div>
    <h1>开始对话</h1>
    <p>{{ hint }}</p>
    <div class="suggestions">
      <button
        v-for="text in suggestions"
        :key="text"
        type="button"
        class="suggestion"
        @click="pick(text)"
      >
        {{ text }}
      </button>
    </div>
  </div>
</template>
