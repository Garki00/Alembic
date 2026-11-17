<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  side: 'left' | 'right'
  open: boolean
}>()

const emit = defineEmits<{ (event: 'toggle'): void }>()

const title = computed(() => {
  const name = props.side === 'left' ? '左侧边栏' : '右侧边栏'
  return props.open ? `收起${name}` : `展开${name}`
})
</script>

<template>
  <button
    type="button"
    class="panel-toggle"
    :class="[`panel-toggle-${side}`, { active: open }]"
    :aria-expanded="open"
    :aria-label="title"
    :title="title"
    @click="emit('toggle')"
  >
    <svg viewBox="0 0 18 18" aria-hidden="true">
      <rect
        x="2.25"
        y="3.25"
        width="13.5"
        height="11.5"
        rx="2.25"
        fill="none"
        stroke="currentColor"
        stroke-width="1.4"
      />
      <path
        :d="side === 'left' ? 'M6.75 3.5v11' : 'M11.25 3.5v11'"
        fill="none"
        stroke="currentColor"
        stroke-width="1.4"
      />
      <path
        class="panel-toggle-arrow"
        d="M9.9 7.2 8.3 9l1.6 1.8"
        fill="none"
        stroke="currentColor"
        stroke-width="1.4"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
    </svg>
  </button>
</template>
