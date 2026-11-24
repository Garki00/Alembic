<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import AboutDialog from './AboutDialog.vue'
import type { DesktopInfo } from '../types/desktop'

const api = window.alembic
const isDesktop = Boolean(api)
const aboutOpen = ref(false)
const maximized = ref(false)
const info = ref<DesktopInfo>({ name: 'ALEMBIC', version: '', packaged: false })

let stopMaximized: (() => void) | null = null

function minimize(): void {
  void api?.minimize()
}

function toggleMaximize(): void {
  if (!api) return
  void api.toggleMaximize().then((result) => {
    maximized.value = Boolean(result && result.maximized)
  })
}

function close(): void {
  void api?.close()
}

onMounted(() => {
  if (!api) return
  document.documentElement.style.setProperty('--desktop-titlebar-height', '38px')
  void api.getInfo().then((value) => {
    if (value) info.value = value
  }).catch(() => { /* keep the fallback title */ })
  void api.isMaximized().then((result) => {
    maximized.value = Boolean(result && result.maximized)
  }).catch(() => { /* non-fatal */ })
  stopMaximized = api.onMaximized((value) => {
    maximized.value = value
  })
})

onBeforeUnmount(() => {
  document.documentElement.style.removeProperty('--desktop-titlebar-height')
  stopMaximized?.()
})
</script>

<template>
  <header v-if="isDesktop" class="app-titlebar" @dblclick="toggleMaximize">
    <div class="titlebar-left">
      <button type="button" class="titlebar-about" @click="aboutOpen = true" @dblclick.stop>关于</button>
    </div>
    <div class="titlebar-controls">
      <button type="button" class="titlebar-control" aria-label="最小化" title="最小化" @click="minimize" @dblclick.stop>
        <svg viewBox="0 0 12 12" aria-hidden="true">
          <path d="M2 6h8" fill="none" stroke="currentColor" stroke-width="1" stroke-linecap="round" />
        </svg>
      </button>
      <button
        type="button"
        class="titlebar-control"
        :aria-label="maximized ? '还原' : '最大化'"
        :title="maximized ? '还原' : '最大化'"
        @click="toggleMaximize"
        @dblclick.stop
      >
        <svg v-if="maximized" viewBox="0 0 12 12" aria-hidden="true">
          <rect x="3.1" y="1.6" width="6.4" height="6.4" rx="0.8" fill="none" stroke="currentColor" stroke-width="1" />
          <path d="M1.9 4.1v5.2a0.8 0.8 0 0 0 0.8 0.8h5.2" fill="none" stroke="currentColor" stroke-width="1" stroke-linecap="round" />
        </svg>
        <svg v-else viewBox="0 0 12 12" aria-hidden="true">
          <rect x="2" y="2" width="8" height="8" rx="0.8" fill="none" stroke="currentColor" stroke-width="1" />
        </svg>
      </button>
      <button type="button" class="titlebar-control titlebar-close" aria-label="关闭" title="关闭" @click="close" @dblclick.stop>
        <svg viewBox="0 0 12 12" aria-hidden="true">
          <path d="M2.5 2.5 9.5 9.5M9.5 2.5 2.5 9.5" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" />
        </svg>
      </button>
    </div>
  </header>
  <AboutDialog
    :open="aboutOpen"
    :name="info.name"
    :version="info.version"
    @close="aboutOpen = false"
  />
</template>

<style scoped>
.app-titlebar {
  -webkit-app-region: drag;
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 38px;
  padding: 0 8px 0 16px;
  border-bottom: 1px solid var(--border);
  background: var(--paper-deep);
  user-select: none;
}

.titlebar-left {
  display: flex;
  align-items: center;
  min-width: 0;
}

.titlebar-about {
  -webkit-app-region: no-drag;
  height: 24px;
  padding: 0 8px;
  border: 1px solid transparent;
  border-radius: 4px;
  background: transparent;
  color: var(--muted);
  font-size: 11px;
  letter-spacing: 0.02em;
}
.titlebar-about:hover {
  border-color: var(--border);
  background: var(--card);
  color: var(--ink);
}

.titlebar-controls {
  display: flex;
  align-items: stretch;
  height: 100%;
}

.titlebar-control {
  -webkit-app-region: no-drag;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 100%;
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  color: var(--muted);
}
.titlebar-control svg {
  width: 12px;
  height: 12px;
}
.titlebar-control:hover {
  background: var(--card);
  color: var(--ink);
}
.titlebar-close:hover {
  background: var(--danger);
  color: var(--on-emerald);
}
</style>
