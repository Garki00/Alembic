<script setup lang="ts">
import { computed, ref } from 'vue'
import { useChatStore } from '../stores/chat'
import { useWorkspaceStore } from '../stores/workspace'
import type { AssistantTurn, WorkEvent } from '../types/chat'

const props = defineProps<{ turn: AssistantTurn }>()
const chat = useChatStore()
const workspace = useWorkspaceStore()

type ToolEvent = Extract<WorkEvent, { kind: 'tool' }>

const TOOL_LABELS: Record<string, string> = {
  read_file: '读取',
  read_document: '读取文档',
  read_image: '读取图片',
  list_dir: '浏览目录',
  search_files: '搜索',
  find_files: '查找文件',
  file_stat: '查看信息',
  write_file: '写入',
  edit_file: '修改',
  apply_patch: '应用补丁',
  move_file: '移动',
  copy_file: '复制',
  delete_file: '删除',
  make_dir: '新建目录',
  run_command: '执行命令',
  start_process: '启动进程',
  stop_process: '停止进程',
  list_processes: '查看进程',
  read_process_logs: '读取日志',
  wait_process: '等待进程',
  fetch_url: '抓取网页',
  web_search: '搜索网页',
  download_file: '下载文件',
  git_status: 'Git 状态',
  git_diff: 'Git 差异',
  git_commit: 'Git 提交',
  todo_write: '更新任务',
  ask_user_question: '询问用户',
  get_current_time: '查询时间',
  list_timezones: '查询时区',
  get_weather: '查询天气',
  mock_lookup: '模拟数据',
}

const mode = computed(() => workspace.preferences.transcript_view)
const isVerbose = computed(() => mode.value === 'verbose')
const isOpen = computed(() => isVerbose.value || props.turn.stepsOpen)
const events = computed(() => props.turn.workEvents || [])
const errorCount = computed(
  () => events.value.filter((event) => event.kind === 'tool' && event.error).length,
)

const rowOverride = ref<Record<string, boolean>>({})

function clip(text: string, limit: number): string {
  const clean = text.replace(/\s+/g, ' ').trim()
  return clean.length > limit ? `${clean.slice(0, limit)}…` : clean
}

function prettyArgs(args: Record<string, unknown>): string {
  const raw = JSON.stringify(args, null, 2)
  return raw.length > 4000 ? `${raw.slice(0, 4000)}\n…[truncated]` : raw
}

function labelOf(event: WorkEvent): string {
  if (event.kind === 'thought') return '思考'
  return TOOL_LABELS[event.name] || '调用工具'
}

function detailOf(event: WorkEvent): string {
  if (event.kind === 'thought') {
    return clip(event.text, mode.value === 'compact' ? 64 : 140)
  }

  const args = event.args || {}
  const path = String(args.path || '')
  const source = String(args.source || '')
  const destination = String(args.destination || '')
  switch (event.name) {
    case 'read_file':
    case 'read_document':
    case 'read_image':
    case 'list_dir':
    case 'file_stat':
    case 'write_file':
    case 'edit_file':
    case 'delete_file':
    case 'make_dir':
      return path || '(工作区根目录)'
    case 'search_files':
      return [args.pattern, args.glob].filter(Boolean).join(' · ') || path
    case 'find_files':
      return [args.pattern, args.path].filter(Boolean).join(' · ') || '工作区'
    case 'web_search':
      return String(args.query || '')
    case 'download_file':
      return String(args.url || args.path || '')
    case 'apply_patch': {
      const lines = String(args.patch || '').split('\n').filter(Boolean).length
      return `${lines} 行补丁`
    }
    case 'move_file':
    case 'copy_file':
      return `${source || '?'} → ${destination || '?'}`
    case 'run_command':
    case 'start_process':
      return String(args.command || '')
    case 'stop_process':
    case 'wait_process':
      return String(args.process_id || '')
    case 'list_processes':
      return '全部'
    case 'read_process_logs':
      return String(args.process_id || '')
    case 'fetch_url':
      return String(args.url || '')
    case 'git_status':
      return '工作区'
    case 'git_diff':
      return args.staged ? '暂存区' : String(args.path || '工作区')
    case 'git_commit':
      return String(args.message || '')
    case 'todo_write': {
      const todos = Array.isArray(args.todos) ? args.todos : []
      return `${todos.length} 项`
    }
    case 'ask_user_question':
      return String(args.question || '')
    case 'get_current_time':
      return String(args.timezone_name || 'local')
    case 'list_timezones':
      return String(args.keyword || '全部')
    case 'get_weather':
      return String(args.city || '')
    default:
      return event.name
  }
}

function rowOpen(event: WorkEvent): boolean {
  if (isVerbose.value) return true
  const override = rowOverride.value[event.id]
  if (override !== undefined) return override
  // 详细模式下默认展开工具参数/结果；思考保持省略，点击再展开。
  return mode.value === 'detailed' && event.kind === 'tool'
}

function toggleRow(event: WorkEvent): void {
  if (isVerbose.value) return
  rowOverride.value[event.id] = !rowOpen(event)
}

function resultText(event: ToolEvent): string {
  const limit = mode.value === 'verbose' ? 4000 : 1200
  return event.result.length > limit ? `${event.result.slice(0, limit)}\n…[truncated]` : event.result
}

function toggle(): void {
  chat.toggleSteps(props.turn)
}
</script>

<template>
  <section
    v-if="events.length"
    class="work-steps"
    :class="[`work-steps-${mode}`, { open: isOpen }]"
  >
    <button
      type="button"
      class="work-steps-head"
      :aria-expanded="isOpen"
      :disabled="isVerbose"
      @click="toggle"
    >
      <span class="work-steps-title">工作步骤</span>
      <span class="work-steps-meta">
        {{ events.length }} 步
        <template v-if="errorCount"> · {{ errorCount }} 个错误</template>
        <template v-if="turn.streaming"> · 运行中</template>
      </span>
      <span v-if="!isVerbose" class="work-steps-chevron" aria-hidden="true">{{ isOpen ? '⌃' : '⌄' }}</span>
    </button>

    <div v-if="isOpen" class="work-steps-body">
      <div
        v-for="event in events"
        :key="event.id"
        class="work-step"
        :class="{ error: event.kind === 'tool' && event.error, thought: event.kind === 'thought' }"
      >
        <button
          type="button"
          class="work-step-line"
          :aria-expanded="rowOpen(event)"
          :disabled="isVerbose"
          @click="toggleRow(event)"
        >
          <span class="work-step-label">{{ labelOf(event) }}</span>
          <span class="work-step-detail">{{ detailOf(event) }}</span>
          <span v-if="event.kind === 'tool' && event.error" class="work-step-error">失败</span>
          <span v-else-if="event.kind === 'tool' && event.liveOutput && !event.result" class="work-step-live">
            输出中
          </span>
          <span v-if="!isVerbose" class="work-step-chevron" aria-hidden="true">
            {{ rowOpen(event) ? '⌃' : '⌄' }}
          </span>
        </button>

        <div v-if="rowOpen(event)" class="work-step-body">
          <template v-if="event.kind === 'thought'">
            <p class="work-thought-full">{{ event.text }}</p>
          </template>
          <template v-else>
            <pre v-if="Object.keys(event.args).length" class="work-step-code">{{ prettyArgs(event.args) }}</pre>
            <pre v-if="event.result" class="work-step-result code" :class="{ error: event.error }">{{ resultText(event) }}</pre>
            <pre v-else-if="event.liveOutput" class="work-step-result code live">{{ event.liveOutput }}</pre>
          </template>
        </div>
      </div>
    </div>
  </section>
</template>
