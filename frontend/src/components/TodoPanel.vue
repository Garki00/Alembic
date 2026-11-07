<script setup lang="ts">
import { computed } from 'vue'
import type { TodoItem } from '../api/types'

const props = defineProps<{ items: TodoItem[] }>()

const done = computed(() => props.items.filter((item) => item.status === 'completed').length)

function mark(status: string): string {
  if (status === 'completed') return '✓'
  if (status === 'in_progress') return '…'
  return ''
}
</script>

<template>
  <aside class="todo-panel" aria-label="任务清单">
    <div class="todo-head">
      <span class="todo-title">任务清单</span>
      <span class="todo-count">{{ done }}/{{ items.length }}</span>
    </div>
    <ol class="todo-list">
      <li v-for="(item, index) in items" :key="index" :class="item.status">
        <span class="todo-mark">{{ mark(item.status) }}</span>
        <span class="todo-content">{{ item.content }}</span>
      </li>
    </ol>
  </aside>
</template>
