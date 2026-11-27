<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import type { ProcessInfo } from '../api/types'
import { useProcessStore } from '../stores/processes'
import ChevronIcon from './ChevronIcon.vue'

const processes = useProcessStore()
const root = ref<HTMLElement | null>(null)
let timer: number | undefined

const running = computed(() => processes.running)
const finished = computed(() => processes.finished)

function durationText(seconds: number): string {
  const total = Math.max(0, Math.round(seconds || 0))
  if (total < 60) return `${total}s`
  const minutes = Math.floor(total / 60)
  const rest = total % 60
  if (minutes < 60) return `${minutes}m ${String(rest).padStart(2, '0')}s`
  const hours = Math.floor(minutes / 60)
  return `${hours}h ${String(minutes % 60).padStart(2, '0')}m`
}

function resultText(task: ProcessInfo): string {
  if (task.running) return '运行中'
  if (task.status === 'stopped') return '已停止'
  if (task.exit_code === null || task.exit_code === undefined) return '已结束'
  return `退出码 ${task.exit_code}`
}

function detailText(task: ProcessInfo): string {
  if (processes.expandedId !== task.id) return ''
  if (processes.detailLoading && !processes.detailLog) return '读取输出中…'
  return processes.detailLog || '(暂无输出)'
}

function onDocumentClick(event: MouseEvent): void {
  if (!processes.panelOpen) return
  const target = event.target as Node | null
  if (root.value && target && root.value.contains(target)) return
  const element = target as HTMLElement | null
  if (element?.closest?.('[data-process-entry]')) return
  processes.closePanel()
}

onMounted(() => {
  document.addEventListener('mousedown', onDocumentClick)
  void processes.refresh()
  timer = window.setInterval(() => {
    if (!processes.panelOpen) return
    void processes.refresh()
    if (processes.expandedId) void processes.loadDetail(processes.expandedId)
  }, 1200)
})

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocumentClick)
  if (timer !== undefined) window.clearInterval(timer)
})
</script>

<template>
  <section ref="root" class="process-panel" aria-label="后台任务">
    <header class="process-panel-head">
      <span>后台任务</span>
      <button type="button" class="icon-btn" aria-label="关闭" @click="processes.closePanel()">✕</button>
    </header>

    <div class="process-scroll">
      <section class="process-group">
        <button type="button" class="process-group-head" @click="processes.runningOpen = !processes.runningOpen">
          <span class="process-group-title">运行中</span>
          <span class="process-group-count">{{ running.length }}</span>
          <ChevronIcon class="process-group-caret" :direction="processes.runningOpen ? 'up' : 'down'" />
        </button>
        <div v-if="processes.runningOpen" class="process-rows">
          <p v-if="!running.length" class="process-empty">没有正在运行的后台任务。</p>
          <div
            v-for="task in running"
            :key="task.id"
            class="process-row"
            :class="{ open: processes.expandedId === task.id }"
          >
            <button type="button" class="process-summary" @click="processes.toggleTask(task.id)">
              <span class="process-terminal">{{ task.terminal }}<small v-if="task.sandboxed"> · 沙盒</small></span>
              <span class="process-command" :title="task.command">{{ task.command }}</span>
              <span class="process-result running">{{ resultText(task) }}</span>
              <span class="process-time">{{ durationText(task.duration_seconds) }}</span>
            </button>
            <div v-if="processes.expandedId === task.id" class="process-detail">
              <div class="process-detail-label">指令</div>
              <pre class="process-command-block">{{ task.command }}</pre>
              <div class="process-detail-label">终端输出</div>
              <pre class="process-output">{{ detailText(task) }}</pre>
            </div>
            <div class="process-row-actions">
              <button type="button" class="btn-mini danger" @click.stop="processes.stopTask(task.id)">
                停止
              </button>
            </div>
          </div>
        </div>
      </section>

      <section class="process-group">
        <button type="button" class="process-group-head" @click="processes.finishedOpen = !processes.finishedOpen">
          <span class="process-group-title">已完成</span>
          <span class="process-group-count">{{ finished.length }}</span>
          <ChevronIcon class="process-group-caret" :direction="processes.finishedOpen ? 'up' : 'down'" />
        </button>
        <div v-if="processes.finishedOpen" class="process-rows">
          <p v-if="!finished.length" class="process-empty">还没有已完成的后台任务。</p>
          <div
            v-for="task in finished"
            :key="task.id"
            class="process-row"
            :class="{ open: processes.expandedId === task.id }"
          >
            <button type="button" class="process-summary" @click="processes.toggleTask(task.id)">
              <span class="process-terminal">{{ task.terminal }}<small v-if="task.sandboxed"> · 沙盒</small></span>
              <span class="process-command" :title="task.command">{{ task.command }}</span>
              <span class="process-result" :class="{ failed: task.status === 'failed' }">{{ resultText(task) }}</span>
              <span class="process-time">{{ durationText(task.duration_seconds) }}</span>
            </button>
            <div v-if="processes.expandedId === task.id" class="process-detail">
              <div class="process-detail-label">指令</div>
              <pre class="process-command-block">{{ task.command }}</pre>
              <div class="process-detail-label">终端输出</div>
              <pre class="process-output">{{ detailText(task) }}</pre>
            </div>
          </div>
        </div>
      </section>
    </div>
  </section>
</template>
