<script setup lang="ts">
import { computed } from 'vue'
import type {
  CollapseTiming,
  LinkTarget,
  PermissionMode,
  Preferences,
  TerminalKind,
  ThemeMode,
  TranscriptViewMode,
} from '../api/types'
import { errorMessage } from '../api/client'
import {
  COLLAPSE_TIMINGS,
  COMPACTION_MODES,
  LINK_TARGETS,
  PERMISSION_MODES,
  TERMINALS,
  THEMES,
  TRANSCRIPT_MODES,
} from '../lib/preferences'
import { useUiStore } from '../stores/ui'
import { useWorkspaceStore } from '../stores/workspace'

const store = useWorkspaceStore()
const ui = useUiStore()

const preferences = computed(() => store.preferences)
const sandbox = computed(() => store.sandbox)
const sandboxConfig = computed(() => store.sandboxConfig)

const MODES = TRANSCRIPT_MODES
const TIMINGS = COLLAPSE_TIMINGS
const PERMISSIONS = PERMISSION_MODES
const LINKS = LINK_TARGETS
const COMPACTIONS = COMPACTION_MODES
const compactionMode = computed(() => (
  store.preferences.compact_keep_first_turn ? 'keep_first_turn' : 'default'
))

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

async function setTheme(event: Event): Promise<void> {
  const element = event.target as HTMLSelectElement
  const value = element.value as ThemeMode
  if (!(await save({ theme: value }, '外观已更新'))) {
    element.value = store.preferences.theme
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

async function setSandboxNetwork(event: Event): Promise<void> {
  const element = event.target as HTMLSelectElement
  try {
    await store.saveSandboxConfig({ network: element.value })
    await store.refreshSandbox()
    ui.showToast('沙盒网络策略已更新')
  } catch (error) {
    ui.showToast(errorMessage(error), true)
    element.value = store.sandboxConfig?.network || 'offline'
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

async function setCompaction(event: Event): Promise<void> {
  const element = event.target as HTMLSelectElement
  const value = element.value === 'keep_first_turn'
  if (!(await save({ compact_keep_first_turn: value }, '压缩选项已更新'))) {
    element.value = store.preferences.compact_keep_first_turn ? 'keep_first_turn' : 'default'
  }
}
</script>

<template>
  <section class="tab-panel" id="tab-general">
    <div class="panel-head">
      <p class="muted">设置保存在本机，重启后仍然生效。</p>
    </div>

    <div class="settings-group">
      <div class="settings-group-title">权限与执行</div>
      <div class="general-list">
        <div class="general-row">
          <div class="general-text">
            <div class="general-title">权限</div>
            <div class="general-desc">选择会话的默认权限模式</div>
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
            <div class="general-desc">命令默认使用的终端</div>
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
      <div class="settings-group-title">上下文</div>
      <div class="general-list">
        <div class="general-row">
          <div class="general-text">
            <div class="general-title">压缩选项</div>
            <div class="general-desc">上下文超过预算时如何处理较早的对话</div>
          </div>
          <div class="general-control">
            <select class="general-select" :value="compactionMode" @change="setCompaction">
              <option v-for="mode in COMPACTIONS" :key="mode.value" :value="mode.value">
                {{ mode.label }}
              </option>
            </select>
            <div class="general-hint">
              {{ COMPACTIONS.find((mode) => mode.value === compactionMode)?.hint }}
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="settings-group">
      <div class="settings-group-title">命令沙盒</div>
      <div class="general-list">
        <div class="general-row">
          <div class="general-text">
            <div class="general-title">运行状态</div>
            <div class="general-desc">命令在沙盒中执行，不可用时直接拒绝</div>
          </div>
          <div class="general-control">
            <span class="general-hint">{{ sandbox?.ready ? '已就绪' : '不可用' }}</span>
            <div v-if="sandbox?.detail" class="general-hint">{{ sandbox.detail }}</div>
            <div v-if="sandbox?.reasons?.length" class="general-hint">
              {{ sandbox.reasons.join('、') }}
            </div>
          </div>
        </div>

        <div class="general-row">
          <div class="general-text">
            <div class="general-title">网络策略</div>
            <div class="general-desc">命令可访问的网络范围</div>
          </div>
          <div class="general-control">
            <select class="general-select" :value="sandboxConfig?.network || 'offline'" @change="setSandboxNetwork">
              <option value="offline">离线</option>
              <option value="allowlist">允许列表</option>
            </select>
            <div class="general-hint">
              {{ sandboxConfig?.allowed_domains?.length || 0 }} 个允许域名
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="settings-group">
      <div class="settings-group-title">显示与链接</div>
      <div class="general-list">
        <div class="general-row">
          <div class="general-text">
            <div class="general-title">外观</div>
            <div class="general-desc">界面配色；跟随系统会随系统深浅色自动切换</div>
          </div>
          <div class="general-control">
            <select class="general-select" :value="preferences.theme || 'light'" @change="setTheme">
              <option v-for="theme in THEMES" :key="theme.value" :value="theme.value">
                {{ theme.label }}
              </option>
            </select>
            <div class="general-hint">
              {{ THEMES.find((theme) => theme.value === (preferences.theme || 'light'))?.hint }}
            </div>
          </div>
        </div>

        <div class="general-row">
          <div class="general-text">
            <div class="general-title">工作步骤展示</div>
            <div class="general-desc">对话里显示多少执行细节</div>
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
            <div class="general-desc">完成的步骤何时自动收起</div>
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
                ? '完全展开时不会自动收起'
                : '只影响已完成的轮次' }}
            </div>
          </div>
        </div>

        <div class="general-row">
          <div class="general-text">
            <div class="general-title">网页链接打开方式</div>
            <div class="general-desc">点击回复里的链接时在哪打开</div>
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
            <div class="general-desc">在对话里显示代码改动和命令记录</div>
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
