<script setup lang="ts">
import { computed } from 'vue'
import { renderMarkdown } from '../lib/markdown'
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
  const element = target?.closest<HTMLElement>('[data-file]')
  const path = element?.dataset.file
  if (!path) return
  event.preventDefault()
  inspector.openFile(path)
}
</script>

<template>
  <div class="message" :class="{ streaming: turn.streaming }">
    <div class="avatar"><SunIcon /></div>
    <div class="content">
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
      <div v-if="turn.error" class="error-note">{{ turn.error }}</div>
    </div>
  </div>
</template>
