<script setup lang="ts">
import { computed } from 'vue'
import { providerOf } from '../lib/providers'

const props = withDefaults(
  defineProps<{ provider: string; size?: number }>(),
  { size: 40 },
)

const template = computed(() => providerOf(props.provider))
const iconSize = computed(() => Math.round(props.size * 0.56))
</script>

<template>
  <span
    class="provider-icon"
    :style="{
      width: `${props.size}px`,
      height: `${props.size}px`,
      color: template.color,
      background: template.background,
    }"
    :title="template.name"
  >
    <svg
      :width="iconSize"
      :height="iconSize"
      viewBox="0 0 24 24"
      role="img"
      :aria-label="template.name"
    >
      <path
        v-for="(path, index) in template.iconPaths"
        :key="index"
        :d="path"
        :fill="template.stroke ? 'none' : 'currentColor'"
        :stroke="template.stroke ? 'currentColor' : 'none'"
        :stroke-width="template.stroke ? 1.8 : 0"
        fill-rule="evenodd"
        clip-rule="evenodd"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
    </svg>
  </span>
</template>
