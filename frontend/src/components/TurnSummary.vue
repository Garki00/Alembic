<script setup lang="ts">
import { computed } from 'vue'
import { useInspectorStore } from '../stores/inspector'
import type { AssistantTurn } from '../types/chat'

const props = defineProps<{ turn: AssistantTurn }>()
const inspector = useInspectorStore()

interface FileChange {
  path: string
  added: number
  removed: number
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
    if (!['write_file', 'edit_file'].includes(call.name)) continue
    const path = String(call.args.path || '').replace(/\\/g, '/')
    if (!path) continue
    const result = props.turn.toolResults.find((item) => item.id === call.id)
      || props.turn.toolResults.find((item) => item.name === call.name)
    if (result?.content?.startsWith('Error:')) continue
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
    files.set(path, current)
  }
  return Array.from(files.values())
})

const totalAdded = computed(() => changes.value.reduce((sum, item) => sum + item.added, 0))
const totalRemoved = computed(() => changes.value.reduce((sum, item) => sum + item.removed, 0))

function open(path: string): void {
  inspector.openFile(path)
}
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
        :title="file.path"
        @click="open(file.path)"
      >
        <span class="turn-summary-file">{{ file.path }}</span>
        <span class="turn-summary-file-stat">+{{ file.added }} −{{ file.removed }}</span>
      </button>
    </div>
  </div>
</template>
