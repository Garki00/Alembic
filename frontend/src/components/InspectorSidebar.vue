<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { beginResize } from '../lib/resize'
import { useInspectorStore } from '../stores/inspector'
import BrowserView from './BrowserView.vue'
import FileExplorer from './FileExplorer.vue'
import FileViewer from './FileViewer.vue'

const inspector = useInspectorStore()
const addOpen = ref(false)
const tabsEl = ref<HTMLElement | null>(null)
const activeTab = computed(() => inspector.activeTab)

watch(
  () => inspector.activeId,
  () => {
    nextTick(() => {
      const container = tabsEl.value
      const active = container?.querySelector<HTMLElement>('.inspector-tab.active')
      if (!container || !active) return
      const left = active.offsetLeft
      const right = left + active.offsetWidth
      if (left < container.scrollLeft) {
        container.scrollLeft = Math.max(0, left - 6)
      } else if (right > container.scrollLeft + container.clientWidth) {
        container.scrollLeft = right - container.clientWidth + 6
      }
    })
  },
  { flush: 'post' },
)

function onResize(event: PointerEvent): void {
  beginResize('right', event, (width) => inspector.setWidth(width))
}

function chooseFiles(): void {
  addOpen.value = false
  inspector.openFiles()
}

function chooseBrowser(): void {
  addOpen.value = false
  inspector.openBrowser()
}
</script>

<template>
  <aside class="inspector">
    <div
      class="inspector-resizer"
      role="separator"
      aria-orientation="vertical"
      aria-label="调整右侧栏宽度"
      title="拖动调整右侧栏宽度"
      @pointerdown="onResize"
    ></div>
    <header class="inspector-bar">
      <div v-if="inspector.tabs.length" ref="tabsEl" class="inspector-tabs">
        <div
          v-for="tab in inspector.tabs"
          :key="tab.id"
          class="inspector-tab"
          :class="{ active: tab.id === inspector.activeId }"
          :title="tab.kind === 'file' ? tab.path : tab.title"
          @click="inspector.setActive(tab.id)"
        >
          <span class="inspector-tab-icon">
            {{ tab.kind === 'files' ? '▤' : tab.kind === 'browser' ? '◎' : tab.diff ? '±' : '·' }}
          </span>
          <span class="inspector-tab-title">{{ tab.title }}</span>
          <button
            type="button"
            class="inspector-tab-close"
            title="关闭标签页"
            @click.stop="inspector.closeTab(tab.id)"
          >
            ×
          </button>
        </div>
      </div>
      <span v-else class="inspector-bar-spacer"></span>
      <div v-if="inspector.canAddTab" class="inspector-add">
        <button
          type="button"
          class="icon-btn inspector-add-btn"
          title="新建标签页"
          @click.stop="addOpen = !addOpen"
        >
          <svg class="icon-plus" viewBox="0 0 16 16" aria-hidden="true">
            <path
              d="M8 3.2v9.6M3.2 8h9.6"
              fill="none"
              stroke="currentColor"
              stroke-width="1.5"
              stroke-linecap="round"
            />
          </svg>
        </button>
        <div v-if="addOpen" class="inspector-add-menu">
          <button type="button" @click="chooseFiles">工作区文件</button>
          <button type="button" @click="chooseBrowser">浏览器</button>
        </div>
      </div>
      <button
        type="button"
        class="icon-btn inspector-close"
        title="收起侧边栏"
        aria-label="收起侧边栏"
        @click="inspector.hide()"
      >
        ×
      </button>
    </header>

    <div class="inspector-body">
      <div v-if="!activeTab" class="inspector-options">
        <button type="button" class="inspector-option" @click="chooseFiles">
          <span class="inspector-option-icon">▤</span>
          <span>
            <strong>工作区文件</strong>
            <small>浏览并预览当前工作区的文件</small>
          </span>
        </button>
        <button type="button" class="inspector-option" @click="chooseBrowser">
          <span class="inspector-option-icon">◎</span>
          <span>
            <strong>浏览器</strong>
            <small>在侧边栏打开网页</small>
          </span>
        </button>
      </div>

      <FileExplorer v-else-if="activeTab.kind === 'files'" />
      <FileViewer v-else-if="activeTab.kind === 'file'" :path="activeTab.path" :diff="activeTab.diff" />
      <BrowserView v-else :tab="activeTab" />
    </div>
  </aside>
</template>
