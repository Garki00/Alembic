<script setup lang="ts">
import { ref, watch } from 'vue'
import { normalizeBrowserUrl, useInspectorStore, type BrowserTab } from '../stores/inspector'

const props = defineProps<{ tab: BrowserTab }>()
const inspector = useInspectorStore()

const address = ref(props.tab.url)
const reloadKey = ref(0)

watch(
  () => props.tab.url,
  (url) => { address.value = url },
)

function open(): void {
  const target = normalizeBrowserUrl(address.value)
  address.value = target
  inspector.updateBrowser(props.tab.id, target)
  reloadKey.value += 1
}

function reload(): void {
  if (!props.tab.url) return
  reloadKey.value += 1
}
</script>

<template>
  <div class="browser-view">
    <form class="browser-bar" @submit.prevent="open">
      <input
        v-model="address"
        type="text"
        spellcheck="false"
        autocomplete="off"
        placeholder="输入网址"
      />
      <button
        type="button"
        class="icon-btn browser-icon"
        title="刷新"
        aria-label="刷新"
        @click="reload"
      >
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M21 12a9 9 0 1 1-2.64-6.36" />
          <path d="M21 3v6h-6" />
        </svg>
      </button>
      <button type="submit" class="icon-btn browser-icon" title="打开" aria-label="打开">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M5 12h14" />
          <path d="m13 6 6 6-6 6" />
        </svg>
      </button>
    </form>
    <iframe
      v-if="tab.url"
      :key="`${tab.id}-${reloadKey}`"
      class="browser-frame"
      :src="tab.url"
      title="浏览器"
      referrerpolicy="no-referrer"
      sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
    ></iframe>
    <div v-else class="browser-blank">
      <span>◎</span>
    </div>
  </div>
</template>
