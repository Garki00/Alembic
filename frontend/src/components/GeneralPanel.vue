<script setup lang="ts">
import { computed } from 'vue'
import type { CollapseTiming, Preferences, TranscriptViewMode } from '../api/types'
import { errorMessage } from '../api/client'
import { COLLAPSE_TIMINGS, TRANSCRIPT_MODES } from '../lib/preferences'
import { useUiStore } from '../stores/ui'
import { useWorkspaceStore } from '../stores/workspace'

const store = useWorkspaceStore()
const ui = useUiStore()

const preferences = computed(() => store.preferences)

const MODES = TRANSCRIPT_MODES
const TIMINGS = COLLAPSE_TIMINGS

async function save(patch: Partial<Preferences>, message: string): Promise<boolean> {
  try {
    await store.savePreferences(patch)
    ui.showToast(message)
    return true
  } catch (error) {
    ui.showToast(errorMessage(error), true)
    return false
  }
}

async function setMode(event: Event): Promise<void> {
  const element = event.target as HTMLSelectElement
  const value = element.value as TranscriptViewMode
  if (!(await save({ transcript_view: value }, '工作步骤展示已更新'))) {
    element.value = store.preferences.transcript_view
  }
}

async function setTiming(event: Event): Promise<void> {
  const element = event.target as HTMLSelectElement
  const value = element.value as CollapseTiming
  if (!(await save({ collapse_timing: value }, '收起时机已更新'))) {
    element.value = store.preferences.collapse_timing
  }
}

async function setDeveloperTools(event: Event): Promise<void> {
  const element = event.target as HTMLInputElement
  const value = element.checked
  if (!(await save({ developer_tools: value }, value ? '已显示代码工作视图' : '已隐藏代码工作视图'))) {
    element.checked = store.preferences.developer_tools
  }
}
</script>

<template>
  <section class="tab-panel" id="tab-general">
    <div class="panel-head">
      <p class="muted">显示偏好保存在本机数据目录，刷新或换窗口后仍然生效。</p>
    </div>

    <div class="general-list">
      <div class="general-row">
        <div class="general-text">
          <div class="general-title">工作步骤展示</div>
          <div class="general-desc">选择希望看到多少工具调用细节。不会隐藏最终答案。</div>
        </div>
        <div class="general-control">
          <select class="general-select" :value="preferences.transcript_view" @change="setMode">
            <option v-for="mode in MODES" :key="mode.value" :value="mode.value">
              {{ mode.label }}
            </option>
          </select>
          <div class="general-hint">
            {{ MODES.find((mode) => mode.value === preferences.transcript_view)?.hint }}
          </div>
        </div>
      </div>

      <div class="general-row">
        <div class="general-text">
          <div class="general-title">工作步骤收起时机</div>
          <div class="general-desc">选择何时自动收起已完成轮次的工作步骤。</div>
        </div>
        <div class="general-control">
          <select
            class="general-select"
            :value="preferences.collapse_timing"
            :disabled="preferences.transcript_view === 'verbose'"
            @change="setTiming"
          >
            <option v-for="timing in TIMINGS" :key="timing.value" :value="timing.value">
              {{ timing.label }}
            </option>
          </select>
          <div class="general-hint">
            {{ preferences.transcript_view === 'verbose'
              ? '完全展开模式下不会自动收起'
              : '仅影响已完成的轮次；手动展开后不会被强制收起' }}
          </div>
        </div>
      </div>

      <div class="general-row">
        <div class="general-text">
          <div class="general-title">显示代码工作视图</div>
          <div class="general-desc">
            开启后，在对话中显示本轮代码改动、命令与工具轨迹，便于检查 Agent 做了什么。
          </div>
        </div>
        <div class="general-control">
          <label class="switch">
            <input
              type="checkbox"
              :checked="preferences.developer_tools"
              @change="setDeveloperTools"
            />
            <span class="track"></span>
            <span>{{ preferences.developer_tools ? '已开启' : '已关闭' }}</span>
          </label>
        </div>
      </div>
    </div>
  </section>
</template>
