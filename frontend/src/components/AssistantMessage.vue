<script setup lang="ts">
import { computed } from 'vue'
import type { ApprovalDecision } from '../api/types'
import { renderMarkdown } from '../lib/markdown'
import type { AssistantTurn, ToolCallView } from '../types/chat'
import { useChatStore } from '../stores/chat'
import ApprovalCard from './ApprovalCard.vue'
import SunIcon from './SunIcon.vue'

const props = defineProps<{ turn: AssistantTurn }>()
const chat = useChatStore()

const html = computed(() => renderMarkdown(props.turn.content))

function truncate(text: string, limit = 40): string {
  const clean = String(text || '').replace(/\s+/g, ' ').trim()
  return clean.length > limit ? `${clean.slice(0, limit)}…` : clean
}

function callLabel(call: ToolCallView): string {
  const args = call.args && Object.keys(call.args).length
    ? JSON.stringify(call.args)
    : ''
  return `${call.name || 'tool'}${args ? ` · ${truncate(args, 36)}` : ''}`
}

function callTitle(call: ToolCallView): string {
  return call.args && Object.keys(call.args).length ? JSON.stringify(call.args) : ''
}

function decide(approvalId: string, decision: ApprovalDecision): void {
  const approval = props.turn.approvals.find((item) => item.id === approvalId)
  if (approval) void chat.decideApproval(approval, decision)
}
</script>

<template>
  <div class="message" :class="{ streaming: turn.streaming }">
    <div class="avatar"><SunIcon /></div>
    <div class="content">
      <div
        v-if="turn.toolCalls.length || turn.toolResults.length"
        class="tool-strip"
      >
        <span
          v-for="(call, index) in turn.toolCalls"
          :key="`call-${index}`"
          class="tool-chip"
          :title="callTitle(call)"
        >
          <span class="tool-dot">☼</span>
          <span>{{ callLabel(call) }}</span>
        </span>
        <span
          v-for="(result, index) in turn.toolResults"
          :key="`result-${index}`"
          class="tool-chip result"
          :title="truncate(result.content, 300)"
        >
          <span>{{ result.name }} · {{ result.content.length }} 字</span>
        </span>
      </div>

      <ApprovalCard
        v-for="approval in turn.approvals"
        :key="approval.id"
        :approval="approval"
        @decide="decide(approval.id, $event)"
      />

      <div class="prose" v-html="html"></div>
      <div v-if="turn.error" class="error-note">{{ turn.error }}</div>
    </div>
  </div>
</template>
