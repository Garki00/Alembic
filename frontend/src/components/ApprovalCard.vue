<script setup lang="ts">
import { computed } from 'vue'
import type { ApprovalDecision } from '../api/types'
import type { ChatApproval } from '../types/chat'

const props = defineProps<{ approval: ChatApproval }>()
const emit = defineEmits<{ decide: [decision: ApprovalDecision] }>()

const title = computed(() => (props.approval.risk === 'exec'
  ? '需要确认 · 执行命令'
  : '需要确认 · 修改文件'))

const resolved = computed(() => props.approval.status !== 'pending')

const resultText = computed(() => {
  switch (props.approval.status) {
    case 'always':
      return '已允许（本会话内同类操作不再询问）'
    case 'once':
      return '已允许本次'
    case 'expired':
      return '等待超时，本次操作已按拒绝处理'
    case 'denied':
      return '已拒绝，工具未执行'
    default:
      return ''
  }
})

function decide(decision: ApprovalDecision): void {
  if (resolved.value || props.approval.submitting) return
  emit('decide', decision)
}
</script>

<template>
  <div class="approval-card" :class="{ resolved }" :data-request-id="approval.id">
    <div class="approval-head">
      <span class="approval-title">{{ title }}</span>
      <code>{{ approval.tool }}</code>
    </div>
    <div class="approval-summary">{{ approval.summary }}</div>
    <pre v-if="approval.preview" class="approval-preview">{{ approval.preview }}</pre>
    <div v-if="!resolved" class="approval-actions">
      <button
        type="button"
        class="btn-mini primary"
        :disabled="approval.submitting"
        @click="decide('once')"
      >
        允许本次
      </button>
      <button
        type="button"
        class="btn-mini"
        :disabled="approval.submitting"
        @click="decide('always')"
      >
        始终允许
      </button>
      <button
        type="button"
        class="btn-mini"
        :disabled="approval.submitting"
        @click="decide('deny')"
      >
        拒绝
      </button>
    </div>
    <div v-else class="approval-result">{{ resultText }}</div>
  </div>
</template>
