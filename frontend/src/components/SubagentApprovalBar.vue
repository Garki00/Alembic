<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ApprovalDecision } from '../api/types'
import { useSubagentStore } from '../stores/subagents'

const store = useSubagentStore()
const expanded = ref<Record<string, boolean>>({})

const pending = computed(() => store.approvals.filter((item) => item.status === 'pending'))

function toggle(id: string): void {
  expanded.value[id] = !expanded.value[id]
}

async function decide(id: string, decision: ApprovalDecision): Promise<void> {
  const approval = store.approvals.find((item) => item.id === id)
  if (!approval) return
  await store.decide(approval, decision)
}
</script>

<template>
  <div v-if="pending.length" class="subagent-approvals" role="region" aria-label="子代理审批">
    <article v-for="approval in pending" :key="approval.id" class="subagent-approval">
      <header class="subagent-approval-head">
        <span class="subagent-approval-tag">子代理请求</span>
        <span class="subagent-approval-tool">{{ approval.tool }}</span>
        <button
          v-if="approval.preview"
          type="button"
          class="btn-mini"
          :aria-expanded="Boolean(expanded[approval.id])"
          @click="toggle(approval.id)"
        >
          {{ expanded[approval.id] ? '收起原文' : '查看原文' }}
        </button>
      </header>
      <p class="subagent-approval-summary">{{ approval.summary || approval.target }}</p>
      <p v-if="approval.reason" class="subagent-approval-reason">{{ approval.reason }}</p>
      <pre v-if="expanded[approval.id] && approval.preview" class="subagent-approval-preview">{{ approval.preview }}</pre>
      <div class="subagent-approval-actions">
        <button type="button" class="btn-ghost" :disabled="approval.submitting" @click="decide(approval.id, 'deny')">
          拒绝
        </button>
        <button type="button" class="btn-outline" :disabled="approval.submitting" @click="decide(approval.id, 'always')">
          始终允许
        </button>
        <button type="button" class="btn-primary" :disabled="approval.submitting" @click="decide(approval.id, 'once')">
          允许本次
        </button>
      </div>
    </article>
  </div>
</template>

<style scoped>
.subagent-approvals {
  position: fixed;
  right: 18px;
  bottom: 18px;
  z-index: 70;
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: min(420px, calc(100vw - 36px));
  max-height: 70vh;
  overflow-y: auto;
}

.subagent-approval {
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--card);
  box-shadow: var(--shadow);
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.subagent-approval-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.subagent-approval-tag {
  font-size: 10px;
  letter-spacing: 0.08em;
  color: var(--emerald-deep);
  border: 1px solid var(--mint);
  border-radius: 999px;
  padding: 1px 7px;
}

.subagent-approval-tool {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--muted);
}

.subagent-approval-head .btn-mini {
  margin-left: auto;
}

.subagent-approval-summary {
  margin: 0;
  font-size: 12.5px;
  color: var(--ink);
}

.subagent-approval-reason {
  margin: 0;
  font-size: 11px;
  color: var(--danger);
}

.subagent-approval-preview {
  margin: 0;
  max-height: 180px;
  overflow: auto;
  padding: 6px 8px;
  border: 1px solid var(--border-soft);
  border-radius: 4px;
  background: var(--paper-deep);
  font-family: var(--mono);
  font-size: 10.5px;
  line-height: 1.55;
  white-space: pre-wrap;
}

.subagent-approval-actions {
  display: flex;
  justify-content: flex-end;
  gap: 6px;
}
</style>
