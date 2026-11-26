<script setup lang="ts">
import { computed, ref } from 'vue'
import type { TodoItem } from '../api/types'

const props = defineProps<{ items: TodoItem[] }>()

const expanded = ref(false)
const done = computed(() => props.items.filter((item) => item.status === 'completed').length)
const percent = computed(() => (
  props.items.length ? Math.round((done.value / props.items.length) * 100) : 0
))

function mark(status: string): string {
  if (status === 'completed') return '✓'
  if (status === 'in_progress') return '…'
  return ''
}
</script>

<template>
  <aside class="todo-panel" :class="{ open: expanded }" aria-label="任务进度">
    <button
      type="button"
      class="todo-head"
      :aria-expanded="expanded"
      @click="expanded = !expanded"
    >
      <span class="todo-title">任务</span>
      <span class="todo-progress">
        <span class="todo-count">{{ done }}/{{ items.length }}</span>
        <span class="todo-bar" aria-hidden="true">
          <span :style="{ width: `${percent}%` }"></span>
        </span>
      </span>
      <span class="todo-toggle" aria-hidden="true">{{ expanded ? '⌄' : '⌃' }}</span>
    </button>
    <ol v-if="expanded" class="todo-list">
      <li v-for="(item, index) in items" :key="index" :class="item.status">
        <span class="todo-mark">{{ mark(item.status) }}</span>
        <span class="todo-content">{{ item.content }}</span>
      </li>
    </ol>
  </aside>
</template>
