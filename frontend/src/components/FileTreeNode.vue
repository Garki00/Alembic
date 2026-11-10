<script setup lang="ts">
import { ref } from 'vue'
import { api, errorMessage } from '../api/client'
import type { WorkspaceDirListing, WorkspaceEntry } from '../api/types'
import { useInspectorStore } from '../stores/inspector'

const props = defineProps<{ entry: WorkspaceEntry; depth: number }>()
const inspector = useInspectorStore()

const expanded = ref(false)
const loading = ref(false)
const error = ref('')
const children = ref<WorkspaceEntry[]>([])

async function toggle(): Promise<void> {
  if (props.entry.type === 'file') {
    inspector.openFile(props.entry.path)
    return
  }
  if (expanded.value) {
    expanded.value = false
    return
  }
  expanded.value = true
  if (children.value.length) return
  loading.value = true
  error.value = ''
  try {
    const data = await api<WorkspaceDirListing>(
      `/api/workspace/files?path=${encodeURIComponent(props.entry.path)}`,
    )
    children.value = data.entries
  } catch (reason) {
    error.value = errorMessage(reason)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="file-node">
    <button
      type="button"
      class="file-node-row"
      :class="{ folder: entry.type === 'dir' }"
      :style="{ paddingLeft: `${8 + depth * 14}px` }"
      :title="entry.path"
      @click="toggle"
    >
      <span class="file-node-icon">
        {{ entry.type === 'dir' ? (expanded ? '▾' : '▸') : '·' }}
      </span>
      <span class="file-node-name">{{ entry.name }}</span>
    </button>
    <div v-if="expanded" class="file-node-children">
      <p v-if="loading" class="file-node-note" :style="{ paddingLeft: `${22 + depth * 14}px` }">
        读取中…
      </p>
      <p v-else-if="error" class="file-node-note error" :style="{ paddingLeft: `${22 + depth * 14}px` }">
        {{ error }}
      </p>
      <p v-else-if="!children.length" class="file-node-note" :style="{ paddingLeft: `${22 + depth * 14}px` }">
        空目录
      </p>
      <FileTreeNode
        v-for="child in children"
        :key="child.path"
        :entry="child"
        :depth="depth + 1"
      />
    </div>
  </div>
</template>
