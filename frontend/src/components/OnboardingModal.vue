<script setup lang="ts">
import { computed, ref } from 'vue'
import type { CollapseTiming, ProviderPayload, TerminalKind, TranscriptViewMode } from '../api/types'
import { errorMessage } from '../api/client'
import { COLLAPSE_TIMINGS, TERMINALS, TRANSCRIPT_MODES } from '../lib/preferences'
import { useUiStore } from '../stores/ui'
import { useWorkspaceStore } from '../stores/workspace'
import ProviderEditor from './ProviderEditor.vue'

const store = useWorkspaceStore()
const ui = useUiStore()

const step = ref<1 | 2>(1)
const saving = ref(false)
const error = ref('')
const editor = ref<{ validate: () => boolean; payload: () => ProviderPayload | null } | null>(null)

const transcriptView = ref<TranscriptViewMode>(store.preferences.transcript_view)
const collapseTiming = ref<CollapseTiming>(store.preferences.collapse_timing)
const terminal = ref<TerminalKind>(store.preferences.terminal)

const terminalOptions = computed(() => {
  const available = new Map(store.terminals.map((item) => [item.value, item.available]))
  return TERMINALS.map((item) => ({
    ...item,
    available: available.get(item.value) ?? true,
  }))
})

const stepTitle = computed(() => (
  step.value === 1 ? '供应商与模型' : '设置工作步骤与终端'
))

function next(): void {
  if (!editor.value?.validate()) {
    error.value = '请检查供应商、Base URL、API Key 与模型列表。'
    return
  }
  error.value = ''
  step.value = 2
}

function back(): void {
  error.value = ''
  step.value = 1
}

async function finish(): Promise<void> {
  const payload = editor.value?.payload()
  if (!payload) {
    step.value = 1
    error.value = '请检查供应商、Base URL、API Key 与模型列表。'
    return
  }
  saving.value = true
  error.value = ''
  try {
    await store.saveProvider(payload)
    // 供应商保存成功就先关掉向导，避免偏好保存失败时重复创建
    store.needsSetup = false
    try {
      await store.savePreferences({
        transcript_view: transcriptView.value,
        collapse_timing: collapseTiming.value,
        terminal: terminal.value,
      })
    } catch (err) {
      ui.showToast(`供应商已保存，但工作步骤偏好保存失败：${errorMessage(err)}`, true)
      return
    }
    ui.showToast('配置完成，开始使用吧')
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="onboarding">
    <div class="onboarding-card" role="dialog" aria-modal="true" aria-labelledby="onboarding-title">
      <header class="onboarding-head">
        <div>
          <div class="eyebrow">WELCOME TO ALEMBIC</div>
          <h2 id="onboarding-title">先完成初始化</h2>
          <p class="onboarding-sub">配置一个供应商与模型后即可开始对话，密钥只保存在本机。</p>
        </div>
        <div class="onboarding-steps" aria-hidden="true">
          <span :class="{ active: step === 1, done: step > 1 }">1 供应商</span>
          <i></i>
          <span :class="{ active: step === 2 }">2 偏好</span>
        </div>
      </header>

      <div class="onboarding-body">
        <section v-show="step === 1" class="onboarding-section">
          <div class="onboarding-section-head">
            <h3>{{ stepTitle }}</h3>
            <p class="muted">
              选择供应商模板会自动填入 Base URL；一个供应商下可以添加多个模型，
              每个模型可单独设置思考等级。
            </p>
          </div>
          <ProviderEditor ref="editor" hide-actions />
        </section>

        <section v-show="step === 2" class="onboarding-section">
          <div class="onboarding-section-head">
            <h3>{{ stepTitle }}</h3>
            <p class="muted">这些设置之后也可以在「设置 → 通用」里随时修改。</p>
          </div>

          <div class="onboarding-prefs">
            <div class="onboarding-pref-row">
              <div>
                <div class="general-title">工作步骤展示</div>
                <div class="general-desc">对话里显示多少执行细节</div>
              </div>
              <select v-model="transcriptView" class="general-select">
                <option v-for="mode in TRANSCRIPT_MODES" :key="mode.value" :value="mode.value">
                  {{ mode.label }}
                </option>
              </select>
            </div>
            <p class="onboarding-note">
              {{ TRANSCRIPT_MODES.find((mode) => mode.value === transcriptView)?.hint }}
            </p>

            <div class="onboarding-pref-row">
              <div>
                <div class="general-title">工作步骤收起时机</div>
                <div class="general-desc">完成的步骤何时自动收起</div>
              </div>
              <select
                v-model="collapseTiming"
                class="general-select"
                :disabled="transcriptView === 'verbose'"
              >
                <option
                  v-for="timing in COLLAPSE_TIMINGS"
                  :key="timing.value"
                  :value="timing.value"
                >
                  {{ timing.label }}
                </option>
              </select>
            </div>
            <p class="onboarding-note">
              {{ transcriptView === 'verbose'
                ? '完全展开时不会自动收起'
                : '只影响已完成的轮次' }}
            </p>

            <div class="onboarding-pref-row">
              <div>
                <div class="general-title">终端偏好</div>
                <div class="general-desc">命令默认使用的终端</div>
              </div>
              <select v-model="terminal" class="general-select">
                <option
                  v-for="item in terminalOptions"
                  :key="item.value"
                  :value="item.value"
                  :disabled="!item.available"
                >
                  {{ item.label }}{{ item.available ? '' : '（未检测到）' }}
                </option>
              </select>
            </div>
            <p class="onboarding-note">
              {{ terminalOptions.find((item) => item.value === terminal)?.hint }}
            </p>
          </div>
        </section>

        <p v-if="error" class="onboarding-error">{{ error }}</p>
      </div>

      <footer class="onboarding-foot">
        <div class="onboarding-foot-hint">
          <template v-if="step === 1">Base URL 与密钥只写入本机数据目录。</template>
          <template v-else>准备就绪后即可开始对话。</template>
        </div>
        <div class="onboarding-actions">
          <button v-if="step === 2" type="button" class="btn-ghost" @click="back">返回</button>
          <button
            v-if="step === 1"
            type="button"
            class="btn-primary"
            @click="next"
          >
            继续
          </button>
          <button
            v-else
            type="button"
            class="btn-primary"
            :disabled="saving"
            @click="finish"
          >
            {{ saving ? '保存中…' : '开始使用' }}
          </button>
        </div>
      </footer>
    </div>
  </div>
</template>
