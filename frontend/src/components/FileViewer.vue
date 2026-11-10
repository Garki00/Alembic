<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, errorMessage } from '../api/client'
import type { WorkspaceFilePreview } from '../api/types'
import { renderMarkdown } from '../lib/markdown'
import { useInspectorStore } from '../stores/inspector'

const props = defineProps<{ path: string }>()
const inspector = useInspectorStore()

const file = ref<WorkspaceFilePreview | null>(null)
const loading = ref(false)
const error = ref('')

const sizeText = computed(() => {
  const size = file.value?.size || 0
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
})

const html = computed(() => (
  file.value?.kind === 'markdown' ? renderMarkdown(file.value.content) : ''
))

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  file.value = null
  try {
    file.value = await api<WorkspaceFilePreview>(
      `/api/workspace/file?path=${encodeURIComponent(props.path)}`,
    )
  } catch (reason) {
    error.value = errorMessage(reason)
  } finally {
    loading.value = false
  }
}

watch(() => props.path, load, { immediate: true })

function openFileLink(event: MouseEvent): void {
  const target = event.target as HTMLElement | null
  const element = target?.closest<HTMLElement>('[data-file]')
  const path = element?.dataset.file
  if (!path) return
  event.preventDefault()
  inspector.openFile(path)
}
</script>

<template>
  <div class="file-viewer">
    <div class="file-viewer-head">
      <span class="file-viewer-path" :title="path">{{ path }}</span>
      <span class="file-viewer-meta">{{ sizeText }}</span>
    </div>

    <p v-if="loading" class="file-explorer-note">读取中…</p>
    <p v-else-if="error" class="file-explorer-note error">{{ error }}</p>
    <div v-else-if="file && !file.supported" class="file-unsupported">
      <strong>不支持预览</strong>
      <span>{{ file.reason || '该格式暂不支持预览' }}</span>
    </div>
    <template v-else-if="file">
      <div v-if="file.kind === 'markdown'" class="prose file-markdown" v-html="html" @click="openFileLink"></div>
      <pre v-else class="file-code">{{ file.content }}</pre>
      <p v-if="file.truncated" class="file-explorer-note">文件较大，只显示前面一部分</p>
    </template>
  </div>
</template>
