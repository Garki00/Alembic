<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, errorMessage } from '../api/client'
import type { WorkspaceFilePreview } from '../api/types'
import { diffLines, diffStats, parsePatch, type DiffRow, type FileDiff } from '../lib/diff'
import { renderMarkdown } from '../lib/markdown'
import { useInspectorStore } from '../stores/inspector'
import DiffView from './DiffView.vue'

const props = defineProps<{ path: string; diff?: FileDiff }>()
const inspector = useInspectorStore()

type ViewMode = 'preview' | 'source' | 'diff'

const file = ref<WorkspaceFilePreview | null>(null)
const loading = ref(false)
const error = ref('')
const mode = ref<ViewMode>('source')

const sizeText = computed(() => {
  const size = file.value?.size || 0
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
})

const isMarkdown = computed(() => file.value?.kind === 'markdown')
const hasDiff = computed(() => Boolean(props.diff))

const html = computed(() => (
  isMarkdown.value ? renderMarkdown(file.value?.content || '') : ''
))

const contentLines = computed<string[]>(() => {
  const text = (file.value?.content || '').replace(/\r\n?/g, '\n')
  if (!text) return []
  const lines = text.split('\n')
  if (lines[lines.length - 1] === '') lines.pop()
  return lines
})

const lineNumbers = computed(() => contentLines.value.map((_, index) => index + 1).join('\n'))

function occurrenceCount(text: string, needle: string): number {
  if (!needle) return 0
  let count = 0
  let index = text.indexOf(needle)
  while (index >= 0) {
    count += 1
    index = text.indexOf(needle, index + needle.length)
  }
  return count
}

const snippetReconstructed = computed(() => {
  const change = props.diff
  if (!change?.snippet || !file.value || !change.after) return false
  return occurrenceCount(file.value.content, change.after) === 1
})

const diffRows = computed<DiffRow[]>(() => {
  const change = props.diff
  if (!change) return []
  if (change.patch) {
    const parsed = parsePatch(change.patch)
    const match = parsed.find((item) => item.path === change.path) || parsed[0]
    return match ? match.rows : []
  }
  let before = change.before || ''
  let after = change.after || ''
  if (snippetReconstructed.value && file.value) {
    const current = file.value.content
    const index = current.indexOf(after)
    before = current.slice(0, index) + before + current.slice(index + after.length)
    after = current
  } else if (change.whole) {
    after = change.after || file.value?.content || ''
  }
  return diffLines(before, after)
})

const diffStat = computed(() => diffStats(diffRows.value))

const diffNote = computed(() => {
  if (props.diff?.note) return props.diff.note
  if (props.diff?.whole) return '整文件写入，旧内容不可用：差异只显示新增内容。'
  if (props.diff?.snippet && !snippetReconstructed.value) {
    return '未能从当前文件重建旧内容，差异按本轮修改片段显示。'
  }
  return ''
})

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  file.value = null
  try {
    file.value = await api<WorkspaceFilePreview>(
      `/api/workspace/file?path=${encodeURIComponent(props.path)}`,
    )
    if (props.diff) mode.value = 'diff'
    else if (file.value.kind === 'markdown') mode.value = 'preview'
    else mode.value = 'source'
  } catch (reason) {
    // 删除类补丁对应的文件可能已经不存在；只要有 diff 参数，仍然展示差异。
    if (props.diff) {
      error.value = ''
      mode.value = 'diff'
    } else {
      error.value = errorMessage(reason)
    }
  } finally {
    loading.value = false
  }
}

watch(() => [props.path, props.diff], load, { immediate: true })

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
      <span v-if="mode === 'diff' && diffRows.length" class="file-viewer-stat">
        +{{ diffStat.added }} −{{ diffStat.removed }}
      </span>
      <div v-if="!loading && !error && (hasDiff || (file && isMarkdown))" class="file-viewer-modes">
        <button
          v-if="hasDiff"
          type="button"
          class="file-viewer-mode"
          :class="{ active: mode === 'diff' }"
          @click="mode = 'diff'"
        >
          差异
        </button>
        <button
          v-if="file && isMarkdown"
          type="button"
          class="file-viewer-mode"
          :class="{ active: mode === 'preview' }"
          @click="mode = 'preview'"
        >
          预览
        </button>
        <button
          v-if="file"
          type="button"
          class="file-viewer-mode"
          :class="{ active: mode === 'source' }"
          @click="mode = 'source'"
        >
          原文
        </button>
      </div>
      <span v-if="file" class="file-viewer-meta">{{ sizeText }}</span>
    </div>

    <p v-if="loading" class="file-explorer-note">读取中…</p>
    <p v-else-if="error && !hasDiff" class="file-explorer-note error">{{ error }}</p>
    <div v-else-if="mode === 'diff'" class="file-diff">
      <p v-if="diffNote" class="file-diff-note">{{ diffNote }}</p>
      <DiffView :rows="diffRows" />
    </div>
    <div v-else-if="file && file.kind === 'image'" class="file-image">
      <img :src="file.content" :alt="file.name" />
    </div>
    <div v-else-if="file && !file.supported" class="file-unsupported">
      <strong>不支持预览</strong>
      <span>{{ file.reason || '该格式暂不支持预览' }}</span>
    </div>
    <template v-else-if="file">
      <div v-if="mode === 'preview'" class="prose file-markdown" v-html="html" @click="openFileLink"></div>
      <div v-else class="code-frame">
        <pre class="code-gutter" aria-hidden="true">{{ lineNumbers }}</pre>
        <pre class="file-code">{{ file.content }}</pre>
      </div>
      <p v-if="file.truncated" class="file-explorer-note">文件较大，只显示前面一部分</p>
    </template>
  </div>
</template>
