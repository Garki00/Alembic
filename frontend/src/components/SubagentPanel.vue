<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import type { SubagentInfo } from '../api/types'
import { useSubagentStore } from '../stores/subagents'

const subagents = useSubagentStore()
const root = ref<HTMLElement | null>(null)
const now = ref(Date.now())
let timer: number | undefined

const running = computed(() => subagents.running)
const finished = computed(() => subagents.finished)

function durationText(task: SubagentInfo): string {
  const start = Number(task.started_at || 0) * 1000
  if (!start) return ''
  const end = task.finished_at ? Number(task.finished_at) * 1000 : now.value
  const total = Math.max(0, Math.round((end - start) / 1000))
  if (total < 60) return `${total}s`
  const minutes = Math.floor(total / 60)
  const rest = total % 60
  if (minutes < 60) return `${minutes}m ${String(rest).padStart(2, '0')}s`
  const hours = Math.floor(minutes / 60)
  return `${hours}h ${String(minutes % 60).padStart(2, '0')}m`
}

const STATUS_LABELS: Record<string, string> = {
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  stopped: '已停止',
}

function statusText(task: SubagentInfo): string {
  return STATUS_LABELS[task.status] || task.status
}

function toolCountText(task: SubagentInfo): string {
  return task.tool_uses ? `${task.tool_uses} 次工具` : ''
}

function onDocumentClick(event: MouseEvent): void {
  if (!subagents.panelOpen) return
  const target = event.target as Node | null
  if (root.value && target && root.value.contains(target)) return
  const element = target as HTMLElement | null
  if (element?.closest?.('[data-subagent-entry]')) return
  subagents.closePanel()
}

onMounted(() => {
  document.addEventListener('mousedown', onDocumentClick)
  void subagents.refresh()
  timer = window.setInterval(() => {
    now.value = Date.now()
    if (!subagents.panelOpen) return
    void subagents.refresh()
  }, 1200)
})

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocumentClick)
  if (timer !== undefined) window.clearInterval(timer)
})
</script>

<template>
  <section ref="root" class="process-panel subagent-panel" aria-label="子代理">
    <header class="process-panel-head">
      <span>子代理</span>
      <button type="button" class="icon-btn" aria-label="关闭" @click="subagents.closePanel()">✕</button>
    </header>

    <div class="process-scroll">
      <section class="process-group">
        <button type="button" class="process-group-head" @click="subagents.runningOpen = !subagents.runningOpen">
          <span class="process-group-title">运行中</span>
          <span class="process-group-count">{{ running.length }}</span>
          <span class="process-group-caret">{{ subagents.runningOpen ? '⌃' : '⌄' }}</span>
        </button>
        <div v-if="subagents.runningOpen" class="process-rows">
          <p v-if="!running.length" class="process-empty">没有正在运行的子代理。</p>
          <div
            v-for="task in running"
            :key="task.id"
            class="process-row"
            :class="{ open: subagents.expandedId === task.id }"
          >
            <div class="subagent-row-line">
              <button type="button" class="process-summary" @click="subagents.toggleTask(task.id)">
                <span class="process-terminal">{{ task.agent_type }}</span>
                <span class="process-command">{{ task.description }}</span>
                <span class="process-result running">{{ statusText(task) }}</span>
                <span class="process-time">{{ durationText(task) }}</span>
              </button>
              <button
                type="button"
                class="icon-btn subagent-open"
                title="在侧边栏打开"
                aria-label="在侧边栏打开"
                @click.stop="subagents.openInSidebar(task.id)"
              >
                <svg viewBox="0 0 16 16" aria-hidden="true">
                  <rect x="2.2" y="3.2" width="11.6" height="9.6" rx="1.2" fill="none" stroke="currentColor" stroke-width="1.2" />
                  <path d="M9.4 3.2v9.6" fill="none" stroke="currentColor" stroke-width="1.2" />
                </svg>
              </button>
            </div>
            <div v-if="subagents.expandedId === task.id" class="process-detail">
              <div class="process-detail-label">任务</div>
              <pre class="process-command-block">{{ task.description }}</pre>
              <div class="process-detail-label">状态</div>
              <p class="subagent-meta">
                {{ statusText(task) }} · {{ durationText(task) }}
                <template v-if="toolCountText(task)"> · {{ toolCountText(task) }}</template>
                <template v-if="task.model"> · {{ task.model }}</template>
              </p>
              <div v-if="task.report" class="process-detail-label">报告</div>
              <pre v-if="task.report" class="process-output">{{ task.report }}</pre>
            </div>
            <div class="process-row-actions">
              <button type="button" class="btn-mini danger" @click.stop="subagents.stopTask(task.id)">
                停止
              </button>
            </div>
          </div>
        </div>
      </section>

      <section class="process-group">
        <button type="button" class="process-group-head" @click="subagents.finishedOpen = !subagents.finishedOpen">
          <span class="process-group-title">已完成</span>
          <span class="process-group-count">{{ finished.length }}</span>
          <span class="process-group-caret">{{ subagents.finishedOpen ? '⌃' : '⌄' }}</span>
        </button>
        <div v-if="subagents.finishedOpen" class="process-rows">
          <p v-if="!finished.length" class="process-empty">还没有已完成的子代理。</p>
          <div
            v-for="task in finished"
            :key="task.id"
            class="process-row"
            :class="{ open: subagents.expandedId === task.id }"
          >
            <div class="subagent-row-line">
              <button type="button" class="process-summary" @click="subagents.toggleTask(task.id)">
                <span class="process-terminal">{{ task.agent_type }}</span>
                <span class="process-command">{{ task.description }}</span>
                <span class="process-result" :class="{ failed: task.status === 'failed' }">{{ statusText(task) }}</span>
                <span class="process-time">{{ durationText(task) }}</span>
              </button>
              <button
                type="button"
                class="icon-btn subagent-open"
                title="在侧边栏打开"
                aria-label="在侧边栏打开"
                @click.stop="subagents.openInSidebar(task.id)"
              >
                <svg viewBox="0 0 16 16" aria-hidden="true">
                  <rect x="2.2" y="3.2" width="11.6" height="9.6" rx="1.2" fill="none" stroke="currentColor" stroke-width="1.2" />
                  <path d="M9.4 3.2v9.6" fill="none" stroke="currentColor" stroke-width="1.2" />
                </svg>
              </button>
            </div>
            <div v-if="subagents.expandedId === task.id" class="process-detail">
              <div class="process-detail-label">任务</div>
              <pre class="process-command-block">{{ task.description }}</pre>
              <div class="process-detail-label">状态</div>
              <p class="subagent-meta">
                {{ statusText(task) }} · {{ durationText(task) }}
                <template v-if="toolCountText(task)"> · {{ toolCountText(task) }}</template>
                <template v-if="task.model"> · {{ task.model }}</template>
              </p>
              <p v-if="task.error" class="subagent-error">{{ task.error }}</p>
              <div v-if="task.report" class="process-detail-label">报告</div>
              <pre v-if="task.report" class="process-output">{{ task.report }}</pre>
            </div>
          </div>
        </div>
      </section>
    </div>
  </section>
</template>
