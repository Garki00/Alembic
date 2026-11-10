<script setup lang="ts">
import { computed } from 'vue'
import type { DiffRow } from '../lib/diff'

const props = withDefaults(
  defineProps<{
    rows: DiffRow[]
    compact?: boolean
    maxRows?: number
  }>(),
  {
    compact: false,
    maxRows: 4000,
  },
)

const shown = computed(() => props.rows.slice(0, props.maxRows))
const hidden = computed(() => Math.max(0, props.rows.length - shown.value.length))

function signOf(row: DiffRow): string {
  if (row.type === 'add') return '+'
  if (row.type === 'remove') return '−'
  if (row.type === 'gap') return '…'
  return ' '
}
</script>

<template>
  <div class="diff-view" :class="{ compact }">
    <div class="diff-scroll">
      <div
        v-for="(row, index) in shown"
        :key="index"
        class="diff-row"
        :class="row.type"
      >
        <span class="diff-num" aria-hidden="true">{{ row.oldLine ?? '' }}</span>
        <span class="diff-num" aria-hidden="true">{{ row.newLine ?? '' }}</span>
        <span class="diff-sign" aria-hidden="true">{{ signOf(row) }}</span>
        <span class="diff-text">{{ row.text || ' ' }}</span>
      </div>
    </div>
    <p v-if="hidden" class="diff-more">还有 {{ hidden }} 行，打开文件查看完整 diff</p>
    <p v-if="!rows.length" class="diff-empty">没有可显示的差异。</p>
  </div>
</template>
