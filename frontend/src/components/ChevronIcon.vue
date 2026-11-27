<script setup lang="ts">
withDefaults(defineProps<{ direction?: 'up' | 'down' | 'left' | 'right' }>(), {
  direction: 'down',
})
</script>

<template>
  <svg
    class="chevron-icon"
    :class="`chevron-${direction}`"
    viewBox="0 0 16 16"
    aria-hidden="true"
  >
    <path
      d="m4.6 6.4 3.4 3.4 3.4-3.4"
      fill="none"
      stroke="currentColor"
      stroke-width="1.4"
      stroke-linecap="round"
      stroke-linejoin="round"
    />
  </svg>
</template>

<style scoped>
.chevron-icon {
  width: 1em;
  height: 1em;
  display: block;
  flex: none;
  transition: transform 140ms ease;
}
.chevron-up { transform: rotate(180deg); }
.chevron-left { transform: rotate(90deg); }
.chevron-right { transform: rotate(-90deg); }
</style>
