<script setup lang="ts">
import { computed } from 'vue'
import type {
  CollapseTiming,
  LinkTarget,
  PermissionMode,
  Preferences,
  TerminalKind,
  TranscriptViewMode,
} from '../api/types'
import { errorMessage } from '../api/client'
import {
  COLLAPSE_TIMINGS,
  LINK_TARGETS,
  PERMISSION_MODES,
  TERMINALS,
  TRANSCRIPT_MODES,
} from '../lib/preferences'
import { useUiStore } from '../stores/ui'
import { useWorkspaceStore } from '../stores/workspace'

const store = useWorkspaceStore()
const ui = useUiStore()

const preferences = computed(() => store.preferences)

const MODES = TRANSCRIPT_MODES
const TIMINGS = COLLAPSE_TIMINGS
const PERMISSIONS = PERMISSION_MODES
const LINKS = LINK_TARGETS

const terminalOptions = computed(() => {
  const available = new Map(store.terminals.map((item) => [item.value, item]))
  return TERMINALS.map((item) => {
    const state = available.get(item.value)
    return {
      ...item,
      available: state?.available ?? true,
      detail: state?.detail || '',
    }
  })
})

const currentTerminalHint = computed(() => (
  terminalOptions.value.find((item) => item.value === preferences.value.terminal)?.hint
  || ''
))

const currentLinkHint = computed(() => (
  LINKS.find((item) => item.value === preferences.value.link_target)?.hint || ''
))

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

async function setPermission(event: Event): Promise<void> {
  const element = event.target as HTMLSelectElement
  const value = element.value as PermissionMode
  if (value === 'full_access' && store.preferences.permission_mode !== 'full_access') {
    const confirmed = window.confirm(
      '完全权限会让 Agent 无需审批执行所有工具和命令。确认切换吗？',
    )
    if (!confirmed) {
      element.value = store.preferences.permission_mode
      return
    }
  }
  if (!(await save({ permission_mode: value }, '权限设置已更新'))) {
    element.value = store.preferences.permission_mode
  }
}

async function setTerminal(event: Event): Promise<void> {
  const element = event.target as HTMLSelectElement
  const value = element.value as TerminalKind
  if (!(await save({ terminal: value }, '终端偏好已更新'))) {
    element.value = store.preferences.terminal
  }
}

async function setLinkTarget(event: Event): Promise<void> {
  const element = event.target as HTMLSelectElement
  const value = element.value as LinkTarget
  if (!(await save({ link_target: value }, '链接打开方式已更新'))) {
    element.value = store.preferences.link_target
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
      <p class="muted">显示与执行偏好保存在本机数据目录，刷新或换窗口后仍然生效。</p>
    </div>

    <div class="settings-group">
      <div class="settings-group-title">权限与执行</div>
      <div class="general-list">
        <div class="general-row">
          <div class="general-text">
            <div class="general-title">权限</div>
            <div class="general-desc">控制 Agent 调用工具前是否需要你审批，以及只读与完全权限的边界。</div>
          </div>
          <div class="general-control">
            <select class="general-select" :value="preferences.permission_mode" @change="setPermission">
              <option v-for="mode in PERMISSIONS" :key="mode.value" :value="mode.value">
                {{ mode.label }}
              </option>
            </select>
            <div class="general-hint">
              {{ PERMISSIONS.find((mode) => mode.value === preferences.permission_mode)?.hint }}
            </div>
          </div>
        </div>

        <div class="general-row">
          <div class="general-text">
            <div class="general-title">终端偏好</div>
            <div class="general-desc">
              命令工具优先使用的终端；模型也可以在单次调用里临时指定其他终端。
            </div>
          </div>
          <div class="general-control">
            <select class="general-select" :value="preferences.terminal" @change="setTerminal">
              <option
                v-for="item in terminalOptions"
                :key="item.value"
                :value="item.value"
                :disabled="!item.available"
              >
                {{ item.label }}{{ item.available ? '' : '（未检测到）' }}
              </option>
            </select>
            <div class="general-hint">{{ currentTerminalHint }}</div>
          </div>
        </div>
      </div>
    </div>

    <div class="settings-group">
      <div class="settings-group-title">显示与链接</div>
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
            <div class="general-title">网页链接打开方式</div>
            <div class="general-desc">AI 回复里的 http(s) 链接点击后在哪里打开。</div>
          </div>
          <div class="general-control">
            <select class="general-select" :value="preferences.link_target" @change="setLinkTarget">
              <option v-for="item in LINKS" :key="item.value" :value="item.value">
                {{ item.label }}
              </option>
            </select>
            <div class="general-hint">{{ currentLinkHint }}</div>
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
    </div>
  </section>
</template>
