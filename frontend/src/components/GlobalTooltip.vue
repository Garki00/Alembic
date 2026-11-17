<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive } from 'vue'

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
  restoreTitle()
}

function place(target: HTMLElement): void {
  const rect = target.getBoundingClientRect()
  const above = rect.top > 52
  state.placement = above ? 'above' : 'below'
  state.top = above ? rect.top - 8 : rect.bottom + 8
  const margin = 12
  const half = 150
  state.left = Math.min(
    Math.max(rect.left + rect.width / 2, margin + half),
    window.innerWidth - margin - half,
  )
}

function activate(target: HTMLElement): void {
  const text = (target.getAttribute('title') || '').trim()
  if (!text) return
  showTimer = 0
  active = target
  pending = null
  target.dataset.tipTitle = text
  target.removeAttribute('title')
  place(target)
  state.text = text
  state.visible = true
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
      v-if="state.visible"
      class="ui-tooltip"
      :class="state.placement"
      :style="{ left: `${state.left}px`, top: `${state.top}px` }"
      role="tooltip"
    >{{ state.text }}</div>
  </Teleport>
</template>
