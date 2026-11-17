<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    open: boolean
    anchor: HTMLElement | null
    width?: number
    height?: number
  }>(),
  { width: 176, height: 196 },
)

const emit = defineEmits<{ (event: 'close'): void }>()

const style = ref<Record<string, string>>({})
const root = ref<HTMLElement | null>(null)
let listening = false

function place(): void {
  const anchor = props.anchor
  if (!anchor) return
  const rect = anchor.getBoundingClientRect()
  const gap = 8
  const maxLeft = window.innerWidth - props.width - 8
  let left = rect.right + gap
  if (left > maxLeft) left = Math.max(8, rect.left - props.width - gap)
  let top = rect.top
  const maxTop = window.innerHeight - props.height - 8
  if (top > maxTop) top = Math.max(8, maxTop)
  style.value = {
    left: `${left}px`,
    top: `${top}px`,
    width: `${props.width}px`,
  }
}

function onDocumentClick(event: MouseEvent): void {
  const target = event.target as Node | null
  if (target && root.value?.contains(target)) return
  if (target && props.anchor?.contains(target)) return
  emit('close')
}

function onViewport(): void {
  if (props.open) place()
}

async function attach(): Promise<void> {
  await nextTick()
  place()
  document.addEventListener('click', onDocumentClick, true)
  document.addEventListener('scroll', onViewport, true)
  window.addEventListener('resize', onViewport)
  listening = true
}

function detach(): void {
  if (!listening) return
  document.removeEventListener('click', onDocumentClick, true)
  document.removeEventListener('scroll', onViewport, true)
  window.removeEventListener('resize', onViewport)
  listening = false
}

watch(
  () => props.open,
  (open) => {
    if (open) void attach()
    else detach()
  },
  { immediate: true },
)

onBeforeUnmount(detach)
</script>

<template>
  <Teleport to="body">
    <div v-if="open" ref="root" class="floating-menu" :style="style" @click.stop>
      <slot />
    </div>
  </Teleport>
</template>
