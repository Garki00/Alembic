<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { copyText } from '../lib/clipboard'
import { renderMarkdown } from '../lib/markdown'
import { formatDuration, formatStamp } from '../lib/time'
import { useInspectorStore } from '../stores/inspector'
import type { AssistantTurn, ChatApproval, ChatQuestion } from '../types/chat'
import { useWorkspaceStore } from '../stores/workspace'
import CodeWorkView from './CodeWorkView.vue'
import SunIcon from './SunIcon.vue'
import TurnSummary from './TurnSummary.vue'
import WorkSteps from './WorkSteps.vue'

const props = defineProps<{ turn: AssistantTurn }>()
const workspace = useWorkspaceStore()
const inspector = useInspectorStore()

const html = computed(() => renderMarkdown(props.turn.content))
const resolvedApprovals = computed(
  () => props.turn.approvals.filter((item) => item.status !== 'pending'),
)
const resolvedQuestions = computed(
  () => props.turn.questions.filter((item) => item.status !== 'pending'),
)

// 运行中每秒刷新一次，只在这个 turn 流式输出时计时
const now = ref(Date.now())
let ticker: number | undefined

function stopTicker(): void {
  if (ticker !== undefined) {
    window.clearInterval(ticker)
    ticker = undefined
  }
}

watch(
  () => props.turn.streaming,
  (streaming) => {
    stopTicker()
    if (!streaming) return
    now.value = Date.now()
    ticker = window.setInterval(() => { now.value = Date.now() }, 1000)
  },
  { immediate: true },
)
onBeforeUnmount(stopTicker)

const startedAt = computed(() => props.turn.startedAt)
const finishedAt = computed(() => props.turn.finishedAt)

const timingText = computed(() => {
  const start = startedAt.value
  if (!start) return ''
  if (props.turn.streaming) return `已用时 ${formatDuration(now.value - start)}`
  const end = finishedAt.value
  if (!end) return ''
  return `已完成，用时 ${formatDuration(end - start)}`
})

const endTimeText = computed(() => formatStamp(finishedAt.value))
const endTimeTitle = computed(() => {
  const ms = finishedAt.value
  if (!ms) return ''
  const date = new Date(ms)
  const pad = (value: number): string => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
})

async function copyAnswer(): Promise<void> {
  await copyText(props.turn.content)
}

function approvalResult(approval: ChatApproval): string {
  switch (approval.status) {
    case 'always':
      return '已允许，本会话内同类操作不再询问'
    case 'once':
      return '已允许本次'
    case 'denied':
      return '已拒绝，工具未执行'
    case 'expired':
      return '等待超时，已按拒绝处理'
    default:
      return ''
  }
}

function questionResult(question: ChatQuestion): string {
  if (question.status === 'answered') return `已回答：${question.answer || '（空）'}`
  if (question.status === 'expired') return '提问超时，已跳过'
  return ''
}

function openFileLink(event: MouseEvent): void {
  const target = event.target as HTMLElement | null
  const file = target?.closest<HTMLElement>('[data-file]')
  const path = file?.dataset.file
  if (path) {
    event.preventDefault()
    inspector.openFile(path)
    return
  }

  const anchor = target?.closest<HTMLAnchorElement>('a[href]')
  const href = anchor?.getAttribute('href') || ''
  if (!anchor || !/^https?:\/\//i.test(href)) return
  event.preventDefault()
  if (workspace.preferences.link_target === 'browser') {
    window.open(href, '_blank', 'noopener,noreferrer')
  } else {
    inspector.openLink(href)
  }
}
</script>

<template>
  <div class="message" :class="{ streaming: turn.streaming }">
    <div class="avatar"><SunIcon /></div>
    <div class="content">
      <div v-if="timingText" class="turn-timing">{{ timingText }}</div>

      <WorkSteps :turn="turn" />

      <div
        v-for="approval in resolvedApprovals"
        :key="approval.id"
        class="decision-note"
      >
        审批结果：{{ approvalResult(approval) }}
      </div>

      <div
        v-for="question in resolvedQuestions"
        :key="question.id"
        class="decision-note"
      >
        {{ questionResult(question) }}
      </div>

      <CodeWorkView v-if="workspace.preferences.developer_tools" :turn="turn" />

      <div class="prose" v-html="html" @click="openFileLink"></div>
      <TurnSummary :turn="turn" />

      <div class="turn-footer">
        <button
          v-if="!turn.streaming && turn.content.trim()"
          type="button"
          class="icon-action"
          title="复制回答"
          aria-label="复制回答"
          @click="copyAnswer"
        >
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <rect x="5.4" y="2.6" width="7.4" height="9" rx="1.2" fill="none" stroke="currentColor" stroke-width="1.2" />
            <path d="M10.4 13.4H4.4c-.7 0-1.2-.5-1.2-1.2V5.6" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" />
          </svg>
        </button>
        <span v-if="endTimeText" class="turn-time" :title="endTimeTitle">{{ endTimeText }}</span>
      </div>

      <div v-if="turn.error" class="error-note">{{ turn.error }}</div>
    </div>
  </div>
</template>
