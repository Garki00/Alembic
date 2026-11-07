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

const parent = computed(() => listing.value?.parent || '')
const dirs = computed(() => listing.value?.dirs || [])

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

async function add(): Promise<void> {
  if (!browserPath.value) return
  try {
    const workspace = await store.addWorkspace(browserPath.value)
    ui.expanded[workspace.id] = true
    close()
    ui.showToast('工作区已添加')
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  }
}

onMounted(() => {
  const active = store.activeWorkspace
  void browse(active?.path)
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
        <form class="workspace-form" @submit.prevent="go">
          <input
            v-model="inputPath"
            placeholder="输入目录路径，例如 D:\work\alembic"
          />
          <button type="submit" class="btn-outline" :disabled="loading">前往</button>
        </form>

        <div class="browser">
          <button
            v-if="parent"
            type="button"
            class="dir-row parent"
            @click="browse(parent)"
          >
            <span class="name">↩ 上一级</span><span class="arrow"></span>
          </button>
          <div v-if="listing && !dirs.length" class="dir-row">
            <span class="name muted">这个目录下没有子目录</span>
          </div>
          <button
            v-for="dir in dirs"
            :key="dir.path"
            type="button"
            class="dir-row"
            @click="browse(dir.path)"
          >
            <span class="name">{{ dir.name }}</span>
            <span class="arrow">↗</span>
          </button>
        </div>

        <div class="form-foot">
          <span class="muted">{{ browserPath }}</span>
          <button
            type="button"
            class="btn-primary"
            :disabled="!browserPath"
            @click="add"
          >
            添加此目录
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
