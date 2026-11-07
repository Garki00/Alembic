<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { errorMessage } from '../api/client'
import type { CollapseTiming, TranscriptViewMode } from '../api/types'
import { COLLAPSE_TIMINGS, TRANSCRIPT_MODES } from '../lib/preferences'
import { providerOf } from '../lib/providers'
import { useUiStore } from '../stores/ui'
import { useWorkspaceStore } from '../stores/workspace'
import ProviderIcon from './ProviderIcon.vue'
import ProviderPicker from './ProviderPicker.vue'

const store = useWorkspaceStore()
const ui = useUiStore()

const step = ref<1 | 2>(1)
const saving = ref(false)
const error = ref('')

const form = reactive({
  provider: 'deepseek',
  name: '',
  model: '',
  base_url: providerOf('deepseek').baseUrl,
  api_key: '',
  temperature: 0,
})

const transcriptView = ref<TranscriptViewMode>(store.preferences.transcript_view)
const collapseTiming = ref<CollapseTiming>(store.preferences.collapse_timing)

const providerTemplate = computed(() => providerOf(form.provider))

const canContinue = computed(() => (
  Boolean(form.model.trim())
  && Boolean(form.base_url.trim())
  && Boolean(form.api_key.trim())
))

const stepTitle = computed(() => (
  step.value === 1 ? '连接一个模型供应商' : '设置工作步骤展示'
))

function chooseProvider(provider: string): void {
  form.provider = provider
  const next = providerOf(provider)
  form.base_url = next.baseUrl
  form.model = ''
  form.name = ''
}

function pickExample(model: string): void {
  form.model = model
}

function next(): void {
  if (!canContinue.value) {
    error.value = '请填写模型 ID、Base URL 和 API Key。'
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
  if (!canContinue.value) {
    step.value = 1
    error.value = '请填写模型 ID、Base URL 和 API Key。'
    return
  }
  saving.value = true
  error.value = ''
  try {
    await store.saveModel({
      id: null,
      name: form.name.trim() || `${providerTemplate.value.short} · ${form.model.trim()}`,
      provider: form.provider,
      model: form.model.trim(),
      base_url: form.base_url.trim(),
      api_key: form.api_key.trim(),
      temperature: Number(form.temperature) || 0,
    })
    // 模型保存成功就先关掉向导，避免偏好保存失败时重复创建模型
    store.needsSetup = false
    try {
      await store.savePreferences({
        transcript_view: transcriptView.value,
        collapse_timing: collapseTiming.value,
      })
    } catch (err) {
      ui.showToast(`模型已保存，但工作步骤偏好保存失败：${errorMessage(err)}`, true)
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
          <p class="onboarding-sub">配置模型供应商后即可开始对话，密钥只保存在本机。</p>
        </div>
        <div class="onboarding-steps" aria-hidden="true">
          <span :class="{ active: step === 1, done: step > 1 }">1 模型</span>
          <i></i>
          <span :class="{ active: step === 2 }">2 工作步骤</span>
        </div>
      </header>

      <div class="onboarding-body">
        <section v-if="step === 1" class="onboarding-section">
          <div class="onboarding-section-head">
            <h3>模型供应商</h3>
            <p class="muted">选择模板会自动填入 Base URL，也可以选「自定义供应商」手动填写。</p>
          </div>
          <ProviderPicker :model-value="form.provider" @update:model-value="chooseProvider" />

          <div class="onboarding-provider-hint">
            <ProviderIcon :provider="form.provider" :size="28" />
            <span>{{ providerTemplate.hint }}</span>
          </div>

          <div class="form-grid onboarding-form">
            <label class="field">
              <span>显示名称 <em class="muted">（可选）</em></span>
              <input
                v-model="form.name"
                :placeholder="`例如：${providerTemplate.short} 主模型`"
                autocomplete="off"
              />
            </label>
            <label class="field">
              <span>模型 ID <em class="req">*</em></span>
              <input
                v-model="form.model"
                :placeholder="providerTemplate.modelPlaceholder"
                autocomplete="off"
              />
            </label>
            <label class="field span-2">
              <span>Base URL <em class="req">*</em></span>
              <input v-model="form.base_url" placeholder="https://api.example.com/v1" />
            </label>
            <label class="field span-2">
              <span>API Key <em class="req">*</em></span>
              <input
                v-model="form.api_key"
                type="password"
                placeholder="sk-…"
                autocomplete="new-password"
              />
            </label>
            <label class="field">
              <span>Temperature</span>
              <input v-model.number="form.temperature" type="number" min="0" max="2" step="0.1" />
            </label>
            <div v-if="providerTemplate.modelExamples.length" class="field">
              <span>常用模型</span>
              <div class="provider-chips">
                <button
                  v-for="example in providerTemplate.modelExamples"
                  :key="example"
                  type="button"
                  class="provider-chip"
                  @click="pickExample(example)"
                >
                  {{ example }}
                </button>
              </div>
            </div>
          </div>
        </section>

        <section v-else class="onboarding-section">
          <div class="onboarding-section-head">
            <h3>{{ stepTitle }}</h3>
            <p class="muted">这两项之后也可以在「设置 → 通用」里随时修改。</p>
          </div>

          <div class="onboarding-prefs">
            <div class="onboarding-pref-row">
              <div>
                <div class="general-title">工作步骤展示</div>
                <div class="general-desc">选择希望看到多少思考与工具调用细节。</div>
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
                <div class="general-desc">选择何时自动收起已完成轮次的工作步骤。</div>
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
                ? '完全展开模式下不会自动收起'
                : '手动展开的轮次不会被强制收起' }}
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
            :disabled="!canContinue"
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
