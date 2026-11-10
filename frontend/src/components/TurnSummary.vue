<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import {
  collapseDiffContext,
  diffLines,
  parsePatch,
  type DiffRow,
  type FileDiff,
} from '../lib/diff'
import { useInspectorStore } from '../stores/inspector'
import type { AssistantTurn } from '../types/chat'
import DiffView from './DiffView.vue'

const props = defineProps<{ turn: AssistantTurn }>()
const inspector = useInspectorStore()

interface FileChange {
  path: string
  added: number
  removed: number
  diff?: FileDiff
}

interface HoverState {
  path: string
  rows: DiffRow[]
  left: number
  top: number
}

function lineCount(text: unknown): number {
  const value = String(text ?? '')
  if (!value) return 0
  return value.split('\n').length
}

function parseStats(result: string): { added: number; removed: number } | null {
  const match = /变更：\+(\d+)\s*-(\d+)/.exec(result || '')
  if (!match) return null
  return { added: Number(match[1]), removed: Number(match[2]) }
}

const changes = computed<FileChange[]>(() => {
  const files = new Map<string, FileChange>()
  for (const call of props.turn.toolCalls) {
    const result = props.turn.toolResults.find((item) => item.id === call.id)
      || props.turn.toolResults.find((item) => item.name === call.name)
    if (result?.content?.startsWith('Error:')) continue

    if (call.name === 'apply_patch') {
      const patch = String(call.args.patch || '')
      for (const item of parsePatch(patch)) {
        const path = item.path.replace(/\\/g, '/')
        const current = files.get(path) || { path, added: 0, removed: 0 }
        current.added += item.added
        current.removed += item.removed
        current.diff = { path, patch }
        files.set(path, current)
      }
      continue
    }

    if (!['write_file', 'edit_file'].includes(call.name)) continue
    const path = String(call.args.path || '').replace(/\\/g, '/')
    if (!path) continue

    let stats = parseStats(result?.content || '')
    if (!stats) {
      if (call.name === 'write_file') {
        stats = { added: lineCount(call.args.content), removed: 0 }
      } else {
        const oldLines = lineCount(call.args.old_string)
        const newLines = lineCount(call.args.new_string)
        stats = { added: newLines, removed: oldLines }
      }
    }
    const current = files.get(path) || { path, added: 0, removed: 0 }
    current.added += stats.added
    current.removed += stats.removed
    if (call.name === 'write_file') {
      current.diff = {
        path,
        before: '',
        after: String(call.args.content || ''),
        whole: true,
      }
    } else {
      current.diff = {
        path,
        before: String(call.args.old_string || ''),
        after: String(call.args.new_string || ''),
        snippet: true,
      }
    }
    files.set(path, current)
  }
  return Array.from(files.values())
})

const totalAdded = computed(() => changes.value.reduce((sum, item) => sum + item.added, 0))
const totalRemoved = computed(() => changes.value.reduce((sum, item) => sum + item.removed, 0))

const hover = ref<HoverState | null>(null)
const HOVER_HIDE_DELAY = 220
let hideTimer: ReturnType<typeof setTimeout> | null = null

function previewRows(change: FileChange): DiffRow[] {
  const diff = change.diff
  if (!diff) return []
  if (diff.patch) {
    const parsed = parsePatch(diff.patch)
    const match = parsed.find((item) => item.path === diff.path) || parsed[0]
    return collapseDiffContext(match ? match.rows : [])
  }
  return collapseDiffContext(diffLines(diff.before || '', diff.after || ''))
}

function open(change: FileChange): void {
  cancelHide()
  hover.value = null
  inspector.openFile(change.path, change.diff)
}

function showHover(change: FileChange, event: Event): void {
  if (!change.diff) return
  cancelHide()
  const element = event.currentTarget as HTMLElement | null
  if (!element) return
  const rect = element.getBoundingClientRect()
  const width = 420
  const left = Math.min(Math.max(8, rect.left), Math.max(8, window.innerWidth - width - 16))
  const estimated = 260
  const top = rect.bottom + estimated + 16 > window.innerHeight
    ? Math.max(8, rect.top - estimated - 6)
    : rect.bottom + 4
  hover.value = {
    path: change.path,
    rows: previewRows(change),
    left,
    top,
  }
}

function cancelHide(): void {
  if (hideTimer === null) return
  clearTimeout(hideTimer)
  hideTimer = null
}

function scheduleHide(): void {
  cancelHide()
  hideTimer = setTimeout(() => {
    hideTimer = null
    hover.value = null
  }, HOVER_HIDE_DELAY)
}

function hideHover(): void {
  cancelHide()
  hover.value = null
}

onBeforeUnmount(cancelHide)
</script>

<template>
  <div v-if="!turn.streaming && changes.length" class="turn-summary">
    <div class="turn-summary-head">
      <span>修改 {{ changes.length }} 个文件</span>
      <span class="turn-summary-stat">+{{ totalAdded }} −{{ totalRemoved }}</span>
    </div>
    <div class="turn-summary-rows">
      <button
        v-for="file in changes"
        :key="file.path"
        type="button"
        class="turn-summary-row"
        :class="{ diffable: Boolean(file.diff) }"
        :title="`${file.path}（点击查看 Diff）`"
        @click="open(file)"
        @mouseenter="showHover(file, $event)"
        @mouseleave="scheduleHide"
        @focus="showHover(file, $event)"
        @blur="hideHover"
      >
        <span class="turn-summary-file">{{ file.path }}</span>
        <span class="turn-summary-file-stat">+{{ file.added }} −{{ file.removed }}</span>
      </button>
    </div>
  </div>

  <Teleport to="body">
    <div
      v-if="hover"
      class="diff-hover"
      :style="{ left: `${hover.left}px`, top: `${hover.top}px`, width: '420px' }"
      role="tooltip"
      @mouseenter="cancelHide"
      @mouseleave="scheduleHide"
    >
      <div class="diff-hover-head">
        <span class="diff-hover-path">{{ hover.path }}</span>
        <span class="diff-hover-hint">Diff 预览</span>
      </div>
      <DiffView :rows="hover.rows" compact :max-rows="hover.rows.length" />
    </div>
  </Teleport>
</template>
