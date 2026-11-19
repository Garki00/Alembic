<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'

/**
 * Site-wide hover label: reads an element's `title`, shows it in one shared
 * tooltip, then restores the attribute once the pointer or focus leaves.
 */
type Placement = 'above' | 'below'

const state = reactive({
  visible: false,
  text: '',
  left: 0,
  top: 0,
  placement: 'above' as Placement,
})

const tipEl = ref<HTMLElement | null>(null)

let active: HTMLElement | null = null
let pending: HTMLElement | null = null
let showTimer = 0
let hideTimer = 0

function clearTimers(): void {
  window.clearTimeout(showTimer)
  window.clearTimeout(hideTimer)
  showTimer = 0
  hideTimer = 0
}

function restoreTitle(): void {
  if (!active) return
  const saved = active.dataset.tipTitle
  if (saved !== undefined) {
    active.setAttribute('title', saved)
    delete active.dataset.tipTitle
  }
  active = null
  pending = null
}

function hide(): void {
  clearTimers()
  state.visible = false
  state.text = ''
  restoreTitle()
}

/**
 * Anchor the label to the target and keep it on screen.
 *
 * The clamped position uses the measured width, so a short label on a button
 * close to a window edge stays next to that button instead of being pushed
 * towards the middle by a fixed assumed width.
 */
async function place(target: HTMLElement): Promise<void> {
  const rect = target.getBoundingClientRect()
  const above = rect.top > 52
  state.placement = above ? 'above' : 'below'
  state.top = above ? rect.top - 8 : rect.bottom + 8
  const center = rect.left + rect.width / 2
  state.left = center
  await nextTick()
  const width = tipEl.value?.offsetWidth ?? 0
  if (width > 0) {
    const margin = 8
    const half = width / 2
    state.left = Math.min(
      Math.max(center, margin + half),
      window.innerWidth - margin - half,
    )
  }
}

async function activate(target: HTMLElement): Promise<void> {
  const text = (target.getAttribute('title') || '').trim()
  if (!text) return
  showTimer = 0
  active = target
  pending = null
  target.dataset.tipTitle = text
  target.removeAttribute('title')
  state.visible = false
  state.text = text
  await place(target)
  if (active === target) state.visible = true
}

function schedule(target: HTMLElement): void {
  if (pending === target && showTimer) return
  clearTimers()
  pending = target
  showTimer = window.setTimeout(() => activate(target), 380)
}

function titled(node: EventTarget | null): HTMLElement | null {
  if (!(node instanceof Element)) return null
  const element = node.closest<HTMLElement>('[title]')
  return element && (element.getAttribute('title') || '').trim() ? element : null
}

function onMouseOver(event: MouseEvent): void {
  if (active) {
    if (active.contains(event.target as Node)) return
    hide()
  }
  const target = titled(event.target)
  if (target) schedule(target)
}

function onMouseOut(event: MouseEvent): void {
  if (active) {
    const related = event.relatedTarget as Node | null
    if (related && active.contains(related)) return
  }
  if (showTimer) {
    window.clearTimeout(showTimer)
    showTimer = 0
    pending = null
  }
  if (!active) return
  hideTimer = window.setTimeout(hide, 120)
}

function onFocusIn(event: FocusEvent): void {
  const target = titled(event.target)
  if (target) {
    clearTimers()
    activate(target)
  }
}

function onFocusOut(): void {
  if (active) hide()
}

function onViewportChange(): void {
  if (active) hide()
}

onMounted(() => {
  document.addEventListener('mouseover', onMouseOver)
  document.addEventListener('mouseout', onMouseOut)
  document.addEventListener('focusin', onFocusIn)
  document.addEventListener('focusout', onFocusOut)
  document.addEventListener('click', onViewportChange, true)
  document.addEventListener('scroll', onViewportChange, true)
  window.addEventListener('resize', onViewportChange)
})

onBeforeUnmount(() => {
  clearTimers()
  document.removeEventListener('mouseover', onMouseOver)
  document.removeEventListener('mouseout', onMouseOut)
  document.removeEventListener('focusin', onFocusIn)
  document.removeEventListener('focusout', onFocusOut)
  document.removeEventListener('click', onViewportChange, true)
  document.removeEventListener('scroll', onViewportChange, true)
  window.removeEventListener('resize', onViewportChange)
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="state.text"
      ref="tipEl"
      class="ui-tooltip"
      :class="state.placement"
      :style="{
        left: `${state.left}px`,
        top: `${state.top}px`,
        visibility: state.visible ? 'visible' : 'hidden',
      }"
      role="tooltip"
    >{{ state.text }}</div>
  </Teleport>
</template>
