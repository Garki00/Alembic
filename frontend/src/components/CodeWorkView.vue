<script setup lang="ts">
import { computed, ref } from 'vue'
import type { AssistantTurn, ToolCallView, ToolResultView } from '../types/chat'

const props = defineProps<{ turn: AssistantTurn }>()
const open = ref(true)

interface CodeEntry {
  id: string
  kind: 'write' | 'edit' | 'command' | 'file'
  title: string
  detail: string
  code: string
  result: string
  error: boolean
}

const KIND_LABELS: Record<CodeEntry['kind'], string> = {
  write: '写入',
  edit: '修改',
  command: '命令',
  file: '文件',
}

const FILE_ACTIONS: Record<string, string> = {
  move_file: '移动',
  copy_file: '复制',
  delete_file: '删除',
  make_dir: '新建目录',
  read_file: '读取',
  read_document: '解析文档',
  read_image: '读取图片',
  list_dir: '浏览目录',
  search_files: '搜索',
  find_files: '查找文件',
  fetch_url: '抓取',
  web_search: '搜索网页',
  download_file: '下载',
}

function findResult(
  call: ToolCallView,
  byId: Map<string, ToolResultView>,
  byName: Map<string, ToolResultView[]>,
): ToolResultView | undefined {
  if (call.id && byId.has(call.id)) {
    const found = byId.get(call.id)
    byId.delete(call.id)
    return found
  }
  return byName.get(call.name)?.shift()
}

function diffLines(oldText: string, newText: string): string {
  const oldLines = oldText.split('\n').map((line) => `- ${line}`)
  const newLines = newText.split('\n').map((line) => `+ ${line}`)
  return [...oldLines, ...newLines].join('\n')
}

function clip(text: string, limit: number): string {
  return text.length > limit ? `${text.slice(0, limit)}\n…[truncated]` : text
}

const entries = computed<CodeEntry[]>(() => {
  const byId = new Map<string, ToolResultView>()
  const byName = new Map<string, ToolResultView[]>()
  for (const result of props.turn.toolResults) {
    if (result.id) byId.set(result.id, result)
    else {
      const queue = byName.get(result.name) || []
      queue.push(result)
      byName.set(result.name, queue)
    }
  }

  const output: CodeEntry[] = []
  for (const call of props.turn.toolCalls) {
    const result = findResult(call, byId, byName)
    const content = result?.content || ''
    const error = content.startsWith('Error:')
    const path = String(call.args.path || '')
    const source = String(call.args.source || '')
    const destination = String(call.args.destination || '')

    if (call.name === 'write_file') {
      output.push({
        id: call.id || `write-${output.length}`,
        kind: 'write',
        title: `写入 ${path || '文件'}`,
        detail: `${String(call.args.content || '').length} 字符`,
        code: clip(String(call.args.content || ''), 4000),
        result: clip(content, 1200),
        error,
      })
    } else if (call.name === 'edit_file') {
      output.push({
        id: call.id || `edit-${output.length}`,
        kind: 'edit',
        title: `修改 ${path || '文件'}`,
        detail: '精确替换',
        code: clip(diffLines(String(call.args.old_string || ''), String(call.args.new_string || '')), 4000),
        result: clip(content, 1200),
        error,
      })
    } else if (call.name === 'apply_patch') {
      const patch = String(call.args.patch || '')
      const first = /^\+\+\+ (?:b\/)?(.+)$/m.exec(patch)?.[1] || '多个文件'
      output.push({
        id: call.id || `patch-${output.length}`,
        kind: 'edit',
        title: `补丁 ${first}`,
        detail: `${patch.split('\n').filter(Boolean).length} 行`,
        code: clip(patch, 4000),
        result: clip(content, 1200),
        error,
      })
    } else if (call.name === 'run_command') {
      output.push({
        id: call.id || `cmd-${output.length}`,
        kind: 'command',
        title: '命令',
        detail: String(call.args.cwd || '工作区根目录'),
        code: `$ ${String(call.args.command || '')}`,
        result: clip(content, 2000),
        error,
      })
    } else if (['move_file', 'copy_file', 'delete_file', 'make_dir', 'download_file'].includes(call.name)) {
      output.push({
        id: call.id || `file-${output.length}`,
        kind: 'file',
        title: FILE_ACTIONS[call.name] || call.name,
        detail: source || destination || path || String(call.args.url || ''),
        code: '',
        result: clip(content, 1200),
        error,
      })
    } else if (['read_file', 'read_document', 'read_image', 'list_dir', 'search_files', 'find_files', 'fetch_url', 'web_search'].includes(call.name)) {
      output.push({
        id: call.id || `read-${output.length}`,
        kind: 'file',
        title: FILE_ACTIONS[call.name] || call.name,
        detail: String(call.args.path || call.args.url || call.args.pattern || call.args.query || ''),
        code: '',
        result: clip(content, 1200),
        error,
      })
    }
  }
  return output
})
</script>

<template>
  <section v-if="entries.length" class="code-work" :class="{ collapsed: !open }">
    <button type="button" class="code-work-head" :aria-expanded="open" @click="open = !open">
      <span class="code-work-title">代码工作视图</span>
      <span class="code-work-meta">本轮 {{ entries.length }} 项</span>
      <span class="work-steps-chevron" aria-hidden="true">{{ open ? '⌃' : '⌄' }}</span>
    </button>

    <div v-if="open" class="code-work-body">
      <article v-for="entry in entries" :key="entry.id" class="code-entry" :class="entry.kind">
        <header class="code-entry-head">
          <span class="code-entry-kind">{{ KIND_LABELS[entry.kind] }}</span>
          <strong>{{ entry.title }}</strong>
          <span v-if="entry.detail" class="code-entry-detail">{{ entry.detail }}</span>
          <span v-if="entry.error" class="work-step-error">失败</span>
        </header>
        <pre v-if="entry.code" class="code-block" :class="{ diff: entry.kind === 'edit' }">{{ entry.code }}</pre>
        <pre v-if="entry.result" class="code-result" :class="{ error: entry.error }">{{ entry.result }}</pre>
      </article>
    </div>
  </section>
</template>
