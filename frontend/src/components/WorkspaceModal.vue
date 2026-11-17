<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { BrowseResult } from '../api/types'
import { errorMessage } from '../api/client'
import { useUiStore } from '../stores/ui'
import { useWorkspaceStore } from '../stores/workspace'

const store = useWorkspaceStore()
const ui = useUiStore()

const inputPath = ref('')
const browserPath = ref('')
const listing = ref<BrowseResult | null>(null)
const loading = ref(false)
const picking = ref(false)
const view = ref<'grid' | 'list'>('grid')

const parent = computed(() => listing.value?.parent || '')
const dirs = computed(() => listing.value?.dirs || [])
const canUp = computed(() => Boolean(parent.value) || Boolean(browserPath.value))

async function browse(path?: string): Promise<void> {
  loading.value = true
  try {
    const data = await store.browseWorkspace(path)
    listing.value = data
    browserPath.value = data.path
    inputPath.value = data.path
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  } finally {
    loading.value = false
  }
}

function go(): void {
  const path = inputPath.value.trim()
  if (path) void browse(path)
}

function close(): void {
  ui.closeWorkspaceModal()
}

async function addPath(path: string): Promise<void> {
  try {
    const workspace = await store.addWorkspace(path)
    ui.expanded[workspace.id] = true
    close()
    ui.showToast('工作区已添加')
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  }
}

async function add(): Promise<void> {
  if (!browserPath.value) return
  await addPath(browserPath.value)
}

async function pick(): Promise<void> {
  if (picking.value) return
  picking.value = true
  try {
    const result = await store.pickWorkspaceFolder(browserPath.value || inputPath.value)
    if (!result.cancelled && result.path) await addPath(result.path)
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  } finally {
    picking.value = false
  }
}

onMounted(() => {
  void browse()
})
</script>

<template>
  <div class="modal" @click.self="close">
    <div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="workspace-title">
      <header class="modal-head">
        <div>
          <div class="eyebrow">WORKSPACE</div>
          <h2 id="workspace-title">添加工作区</h2>
        </div>
        <button type="button" class="icon-btn" aria-label="关闭" @click="close">✕</button>
      </header>
      <div class="workspace-body">
        <form class="workspace-location" @submit.prevent="go">
          <button
            type="button"
            class="icon-btn location-btn"
            title="上一级"
            aria-label="上一级"
            :disabled="!canUp || picking"
            @click="parent ? browse(parent) : browse()"
          >
            <svg viewBox="0 0 16 16" aria-hidden="true">
              <path d="M8 3.5v9M4.5 8 8 4.5 11.5 8" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          </button>
          <input
            v-model="inputPath"
            class="location-input"
            placeholder="路径"
            autocomplete="off"
            :disabled="picking"
          />
          <button type="submit" class="icon-btn location-btn" title="前往" aria-label="前往" :disabled="picking">
            <svg viewBox="0 0 16 16" aria-hidden="true">
              <path d="M3 8h9M8.5 4.5 12 8l-3.5 3.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          </button>
          <button
            type="button"
            class="icon-btn location-btn"
            title="在文件资源管理器中打开"
            aria-label="在文件资源管理器中打开"
            :disabled="picking"
            @click="pick"
          >
            <svg viewBox="0 0 16 16" aria-hidden="true">
              <path d="M1.8 5.2h4l1-1.2h6.4c.5 0 .8.3.8.8v6.4c0 .5-.3.8-.8.8H2.6c-.5 0-.8-.3-.8-.8V5.2Z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round" />
              <path d="M2.2 6.6h11.8" fill="none" stroke="currentColor" stroke-width="1.1" />
            </svg>
          </button>
          <div class="workspace-view-switch" role="group" aria-label="文件视图">
            <button
              type="button"
              class="workspace-view-btn"
              :class="{ active: view === 'grid' }"
              title="网格视图"
              @click="view = 'grid'"
            >
              <svg viewBox="0 0 16 16" aria-hidden="true">
                <rect x="3" y="3" width="4" height="4" rx="0.8" fill="currentColor" />
                <rect x="9" y="3" width="4" height="4" rx="0.8" fill="currentColor" />
                <rect x="3" y="9" width="4" height="4" rx="0.8" fill="currentColor" />
                <rect x="9" y="9" width="4" height="4" rx="0.8" fill="currentColor" />
              </svg>
            </button>
            <button
              type="button"
              class="workspace-view-btn"
              :class="{ active: view === 'list' }"
              title="列表视图"
              @click="view = 'list'"
            >
              <svg viewBox="0 0 16 16" aria-hidden="true">
                <rect x="3" y="3.4" width="2.4" height="2.4" rx="0.6" fill="currentColor" />
                <rect x="7" y="3.8" width="6" height="1.6" rx="0.8" fill="currentColor" />
                <rect x="3" y="6.8" width="2.4" height="2.4" rx="0.6" fill="currentColor" />
                <rect x="7" y="7.2" width="6" height="1.6" rx="0.8" fill="currentColor" />
                <rect x="3" y="10.2" width="2.4" height="2.4" rx="0.6" fill="currentColor" />
                <rect x="7" y="10.6" width="6" height="1.6" rx="0.8" fill="currentColor" />
              </svg>
            </button>
          </div>
        </form>

        <div v-if="picking" class="workspace-waiting">等待选择…</div>
        <div v-else class="workspace-browser" :class="view">
          <p v-if="loading" class="muted workspace-note">读取中…</p>
          <p v-else-if="!dirs.length" class="muted workspace-note">这个目录下没有子目录</p>
          <template v-else>
            <button
              v-for="dir in dirs"
              :key="dir.path"
              type="button"
              class="dir-row"
              @click="browse(dir.path)"
            >
              <span class="dir-icon">
                <svg viewBox="0 0 18 18" aria-hidden="true">
                  <path d="M2 5.4h5l1.2-1.5h7.6c.6 0 1 .4 1 .9v8.1c0 .5-.4.9-1 .9H2c-.6 0-1-.4-1-.9V6.3c0-.5.4-.9 1-.9Z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round" />
                  <path d="M2.4 7.2h14.8" fill="none" stroke="currentColor" stroke-width="1" />
                </svg>
              </span>
              <span class="name">{{ dir.name }}</span>
            </button>
          </template>
        </div>

        <div class="form-foot">
          <span class="muted">{{ browserPath }}</span>
          <button
            type="button"
            class="btn-primary"
            :disabled="!browserPath || picking"
            @click="add"
          >
            添加此目录
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
