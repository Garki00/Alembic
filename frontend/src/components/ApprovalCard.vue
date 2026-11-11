<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ApprovalDecision } from '../api/types'
import type { ChatApproval } from '../types/chat'

const props = defineProps<{ approval: ChatApproval }>()
const emit = defineEmits<{ decide: [decision: ApprovalDecision] }>()

const title = computed(() => {
  if (props.approval.tool === 'start_process') return '需要确认 · 启动后台进程'
  if (props.approval.tool === 'stop_process') return '需要确认 · 停止进程'
  if (props.approval.risk === 'exec') return '需要确认 · 执行命令'
  if (props.approval.risk === 'read') return '需要确认 · 读取或调用工具'
  if (props.approval.tool === 'delete_file') return '需要确认 · 删除文件'
  if (['move_file', 'copy_file', 'make_dir'].includes(props.approval.tool)) {
    return '需要确认 · 整理文件'
  }
  return '需要确认 · 修改文件'
})

const steps = computed(() => props.approval.steps || [])
const showSteps = computed(() => steps.value.length > 1)

// 命令原文与文件改动默认展开：说明归说明，原文始终要能看到。
const DETAIL_OPEN_TOOLS = ['run_command', 'start_process', 'write_file', 'edit_file', 'apply_patch']
const detailOpen = ref(DETAIL_OPEN_TOOLS.includes(props.approval.tool))

const detailLabel = computed(() => {
  let label: string
  switch (props.approval.tool) {
    case 'run_command':
    case 'start_process':
      label = '原始命令'
      break
    case 'write_file':
      label = '将要写入的内容'
      break
    case 'edit_file':
    case 'apply_patch':
      label = '改动内容'
      break
    case 'git_commit':
      label = '提交说明'
      break
    default:
      label = '详细信息'
  }
  return `${detailOpen.value ? '收起' : '查看'}${label}`
})

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

function toggleDetail(): void {
  detailOpen.value = !detailOpen.value
}

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
    <p class="approval-summary">{{ approval.summary }}</p>
    <ol v-if="showSteps" class="approval-steps">
      <li v-for="(step, index) in steps" :key="index">{{ step }}</li>
    </ol>
    <div v-if="approval.reason" class="approval-reason">
      <span class="approval-reason-label">风险提示</span>
      <span>{{ approval.reason }}</span>
    </div>
    <div v-if="approval.preview" class="approval-detail">
      <button
        type="button"
        class="approval-detail-toggle"
        :aria-expanded="detailOpen"
        @click="toggleDetail"
      >
        <span>{{ detailLabel }}</span>
        <span class="approval-detail-chevron" aria-hidden="true">{{ detailOpen ? '⌃' : '⌄' }}</span>
      </button>
      <pre v-if="detailOpen" class="approval-preview">{{ approval.preview }}</pre>
    </div>
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
