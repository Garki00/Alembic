<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    open: boolean
    anchor: HTMLElement | null
    width?: number
    height?: number
    /** `right` opens beside the anchor; `top` opens above it, right-aligned. */
    placement?: 'right' | 'top'
  }>(),
  { width: 176, height: 196, placement: 'right' },
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
  const maxTop = window.innerHeight - props.height - 8
  if (props.placement === 'top') {
    const maxLeft = window.innerWidth - props.width - 8
    const left = Math.max(8, Math.min(rect.right - props.width, maxLeft))
    // Prefer above the anchor; fall back to below when there is not enough room.
    let top = rect.top - props.height - gap
    if (top < 8) top = Math.max(8, Math.min(rect.bottom + gap, maxTop))
    style.value = {
      left: `${left}px`,
      top: `${top}px`,
      width: `${props.width}px`,
    }
    return
  }
  const maxLeft = window.innerWidth - props.width - 8
  let left = rect.right + gap
  if (left > maxLeft) left = Math.max(8, rect.left - props.width - gap)
  let top = rect.top
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
