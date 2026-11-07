<script setup lang="ts">
import { computed } from 'vue'
import type { ApprovalDecision } from '../api/types'
import { renderMarkdown } from '../lib/markdown'
import type { AssistantTurn } from '../types/chat'
import { useChatStore } from '../stores/chat'
import { useWorkspaceStore } from '../stores/workspace'
import ApprovalCard from './ApprovalCard.vue'
import CodeWorkView from './CodeWorkView.vue'
import QuestionCard from './QuestionCard.vue'
import SunIcon from './SunIcon.vue'
import WorkSteps from './WorkSteps.vue'

const props = defineProps<{ turn: AssistantTurn }>()
const chat = useChatStore()
const workspace = useWorkspaceStore()

const html = computed(() => renderMarkdown(props.turn.content))

function decide(approvalId: string, decision: ApprovalDecision): void {
  const approval = props.turn.approvals.find((item) => item.id === approvalId)
  if (approval) void chat.decideApproval(approval, decision)
}

function answer(questionId: string, value: string): void {
  const question = props.turn.questions.find((item) => item.id === questionId)
  if (question) void chat.answerQuestion(question, value)
}
</script>

<template>
  <div class="message" :class="{ streaming: turn.streaming }">
    <div class="avatar"><SunIcon /></div>
    <div class="content">
      <WorkSteps :turn="turn" />

      <ApprovalCard
        v-for="approval in turn.approvals"
        :key="approval.id"
        :approval="approval"
        @decide="decide(approval.id, $event)"
      />

      <QuestionCard
        v-for="question in turn.questions"
        :key="question.id"
        :question="question"
        @answer="answer(question.id, $event)"
      />

      <CodeWorkView v-if="workspace.preferences.developer_tools" :turn="turn" />

      <div class="prose" v-html="html"></div>
      <div v-if="turn.error" class="error-note">{{ turn.error }}</div>
    </div>
  </div>
</template>
