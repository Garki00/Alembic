<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, errorMessage } from '../api/client'
import type { WorkspaceDirListing, WorkspaceEntry } from '../api/types'
import FileTreeNode from './FileTreeNode.vue'

const entries = ref<WorkspaceEntry[]>([])
const loading = ref(false)
const error = ref('')
const truncated = ref(false)

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const data = await api<WorkspaceDirListing>('/api/workspace/files')
    entries.value = data.entries
    truncated.value = data.truncated
  } catch (reason) {
    entries.value = []
    error.value = errorMessage(reason)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="file-explorer">
    <div class="file-explorer-head">
      <span>工作区</span>
      <button
        type="button"
        class="icon-btn file-explorer-refresh"
        :disabled="loading"
        title="刷新"
        aria-label="刷新"
        @click="load"
      >
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M21 12a9 9 0 1 1-2.64-6.36" />
          <path d="M21 3v6h-6" />
        </svg>
      </button>
    </div>
    <p v-if="loading" class="file-explorer-note">读取中…</p>
    <p v-else-if="error" class="file-explorer-note error">{{ error }}</p>
    <p v-else-if="!entries.length" class="file-explorer-note">工作区是空的</p>
    <div v-else class="file-tree">
      <FileTreeNode
        v-for="entry in entries"
        :key="entry.path"
        :entry="entry"
        :depth="0"
      />
    </div>
    <p v-if="truncated" class="file-explorer-note">条目过多，只显示前 500 项</p>
  </div>
</template>
