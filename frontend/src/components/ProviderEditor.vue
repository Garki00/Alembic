<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import type {
  ProviderInfo,
  ProviderModelInfo,
  ProviderModelPayload,
  ProviderPayload,
  ThinkingLevel,
} from '../api/types'
import { errorMessage } from '../api/client'
import { providerOf } from '../lib/providers'
import {
  DEFAULT_THINKING_LEVELS,
  THINKING_OPTIONS,
  THINKING_ORDER,
  levelsSummary,
  normalizeThinkingLevels,
} from '../lib/thinking'
import { useUiStore } from '../stores/ui'
import { useWorkspaceStore } from '../stores/workspace'
import ProviderPicker from './ProviderPicker.vue'

interface ModelRow {
  key: string
  id: string
  model: string
  name: string
  thinking: ThinkingLevel
  thinking_levels: ThinkingLevel[]
  temperature: number
  supports_reasoning: boolean
  supports_images: boolean
  context_length: number
  max_output_tokens: number
  expanded: boolean
}

const props = withDefaults(
  defineProps<{
    initial?: ProviderInfo | null
    hideActions?: boolean
    busy?: boolean
    submitLabel?: string
  }>(),
  {
    initial: null,
    hideActions: false,
    busy: false,
    submitLabel: '保存供应商',
  },
)

const emit = defineEmits<{
  (event: 'submit', payload: ProviderPayload): void
  (event: 'cancel'): void
}>()

const store = useWorkspaceStore()
const ui = useUiStore()

let rowSeq = 0

function modelDefaults(model: string, provider: string) {
  const text = `${provider} ${model}`.toLowerCase()
  let context = 128000
  let output = 8192
  if (text.includes('gpt-5')) [context, output] = [400000, 128000]
  else if (text.includes('gpt-4.1')) [context, output] = [1048576, 32768]
  else if (text.includes('gpt-4o') || text.includes('gpt-4-turbo')) [context, output] = [128000, 16384]
  else if (/(^|\W)(o1|o3|o4)(\W|$)/.test(text)) [context, output] = [200000, 100000]
  else if (text.includes('claude')) [context, output] = [200000, 64000]
  else if (text.includes('gemini')) [context, output] = [1048576, 8192]
  else if (text.includes('qwen')) [context, output] = [131072, 8192]
  else if (text.includes('deepseek')) [context, output] = [128000, 8192]
  else if (text.includes('glm')) [context, output] = [128000, 4096]

  const images = [
    'gpt-4o', 'gpt-4.1', 'gpt-5', 'claude', 'gemini',
    'vision', 'qwen-vl', 'qwen2.5-vl', 'vl-', 'omni', 'llava',
  ].some((token) => text.includes(token))
  return { context, output, images }
}

function emptyModel(): ModelRow {
  rowSeq += 1
  return {
    key: `model-${rowSeq}`,
    id: '',
    model: '',
    name: '',
    thinking: 'high',
    thinking_levels: [...DEFAULT_THINKING_LEVELS],
    temperature: 0,
    supports_reasoning: true,
    supports_images: false,
    context_length: 0,
    max_output_tokens: 0,
    expanded: false,
  }
}

function toRow(model: ProviderModelInfo): ModelRow {
  rowSeq += 1
  const normalized = normalizeThinkingLevels(model.thinking_levels, model.thinking)
  return {
    key: `model-${rowSeq}`,
    id: model.id || '',
    model: model.model,
    name: model.name || '',
    thinking: normalized.thinking,
    thinking_levels: normalized.levels,
    temperature: model.temperature ?? 0,
    supports_reasoning: model.supports_reasoning ?? true,
    supports_images: model.supports_images ?? false,
    context_length: model.context_length || 0,
    max_output_tokens: model.max_output_tokens || 0,
    expanded: false,
  }
}

const form = reactive({
  provider: props.initial?.provider || 'deepseek',
  name: props.initial?.name || '',
  base_url: props.initial?.base_url || providerOf(props.initial?.provider || 'deepseek').baseUrl,
  api_key: '',
  models: (props.initial?.models || []).map(toRow) as ModelRow[],
})

const providerTemplate = computed(() => providerOf(form.provider))
const apiKeyPlaceholder = computed(() => (
  props.initial?.api_key_set ? `${props.initial.api_key_hint}（留空不修改）` : ''
))
const fetching = ref(false)
const localError = ref('')

function chooseProvider(provider: string): void {
  if (form.provider === provider) return
  form.provider = provider
  form.base_url = providerOf(provider).baseUrl
}

function addModel(): void {
  form.models.push(emptyModel())
}

function removeModel(index: number): void {
  form.models.splice(index, 1)
}

function toggleAdvanced(row: ModelRow): void {
  row.expanded = !row.expanded
}

function applyModelDefaults(row: ModelRow): void {
  if (!row.model.trim()) return
  const limits = modelDefaults(row.model, form.provider)
  if (!row.context_length) row.context_length = limits.context
  if (!row.max_output_tokens) row.max_output_tokens = limits.output
  if (!row.supports_images) row.supports_images = limits.images
}

function toggleLevel(row: ModelRow, level: ThinkingLevel, event: Event): void {
  const checked = (event.target as HTMLInputElement).checked
  const chosen = new Set(row.thinking_levels)
  if (checked) chosen.add(level)
  else chosen.delete(level)
  if (!chosen.size) chosen.add('none')
  const levels = THINKING_ORDER.filter((item) => chosen.has(item))
  const normalized = normalizeThinkingLevels(levels, row.thinking, levels)
  row.thinking_levels = normalized.levels
  row.thinking = normalized.thinking
}

async function fetchUpstream(): Promise<void> {
  localError.value = ''
  if (!form.base_url.trim()) {
    localError.value = '请先填写 Base URL。'
    return
  }
  if (!form.api_key.trim() && !props.initial?.api_key_set) {
    localError.value = '请先填写 API Key。'
    return
  }
  fetching.value = true
  try {
    const data = await store.fetchUpstreamModels({
      provider_id: props.initial?.id,
      provider: form.provider,
      base_url: form.base_url.trim(),
      api_key: form.api_key.trim(),
    })
    const existing = new Set(form.models.map((row) => row.model.trim()).filter(Boolean))
    let added = 0
    let enriched = 0
    for (const model of data.models || []) {
      if (!model || existing.has(model)) continue
      const row = emptyModel()
      row.model = model
      applyModelDefaults(row)
      const meta = data.metadata?.[model]
      if (meta) {
        if (meta.context_length) row.context_length = meta.context_length
        if (meta.max_output_tokens) row.max_output_tokens = meta.max_output_tokens
        if (typeof meta.supports_images === 'boolean') row.supports_images = meta.supports_images
        if (typeof meta.supports_reasoning === 'boolean') row.supports_reasoning = meta.supports_reasoning
      }
      form.models.push(row)
      existing.add(model)
      added += 1
      if (meta) enriched += 1
    }
    ui.showToast(
      added
        ? `已从上游添加 ${added} 个模型${enriched ? `（${enriched} 个带能力信息）` : ''}`
        : '上游模型都已在列表中',
    )
  } catch (err) {
    localError.value = errorMessage(err)
  } finally {
    fetching.value = false
  }
}

function validate(): boolean {
  localError.value = ''
  if (!form.base_url.trim()) {
    localError.value = '请填写 Base URL。'
    return false
  }
  if (!form.api_key.trim() && !props.initial?.api_key_set) {
    localError.value = '请填写 API Key。'
    return false
  }
  if (!form.models.length) {
    localError.value = '请至少添加一个模型。'
    return false
  }
  const seen = new Set<string>()
  for (const row of form.models) {
    const model = row.model.trim()
    if (!model) {
      localError.value = '模型 ID 不能为空。'
      return false
    }
    if (seen.has(model)) {
      localError.value = `模型 ${model} 重复了。`
      return false
    }
    seen.add(model)
  }
  return true
}

function payload(): ProviderPayload | null {
  if (!validate()) return null
  const models: ProviderModelPayload[] = form.models.map((row) => ({
    id: row.id || null,
    model: row.model.trim(),
    name: row.name.trim(),
    thinking: row.thinking,
    thinking_levels: row.thinking_levels,
    temperature: Number(row.temperature) || 0,
    supports_reasoning: row.supports_reasoning,
    supports_images: row.supports_images,
    context_length: Number(row.context_length) || 0,
    max_output_tokens: Number(row.max_output_tokens) || 0,
  }))
  return {
    id: props.initial?.id ?? null,
    provider: form.provider,
    name: form.name.trim(),
    base_url: form.base_url.trim(),
    api_key: form.api_key.trim(),
    models,
  }
}

function submit(): void {
  const data = payload()
  if (data) emit('submit', data)
}

defineExpose({ validate, payload })
</script>

<template>
  <div class="provider-editor">
    <div class="field span-2">
      <span>供应商</span>
      <ProviderPicker :model-value="form.provider" @update:model-value="chooseProvider" />
      <div class="provider-hint">{{ providerTemplate.hint }}</div>
    </div>

    <div class="form-grid provider-connection">
      <label class="field">
        <span>显示名称 <em class="muted">（可选）</em></span>
        <input
          v-model="form.name"
          :placeholder="`例如：${providerTemplate.short}`"
          autocomplete="off"
        />
      </label>
      <label class="field">
        <span>API Key <em class="req">*</em></span>
        <input
          v-model="form.api_key"
          type="password"
          :placeholder="apiKeyPlaceholder"
          autocomplete="new-password"
        />
      </label>
      <label class="field span-2">
        <span>Base URL <em class="req">*</em></span>
        <div class="inline">
          <input
            v-model="form.base_url"
            :placeholder="providerTemplate.baseUrl || 'https://api.example.com/v1'"
          />
          <button
            type="button"
            class="btn-outline"
            :disabled="fetching"
            @click="fetchUpstream"
          >
            {{ fetching ? '获取中…' : '从上游获取' }}
          </button>
        </div>
      </label>
    </div>

    <div class="provider-models">
      <div class="provider-models-head">
        <div>
          <div class="general-title">模型列表</div>
          <div class="general-desc">从上游获取或手动添加；左侧箭头展开模型能力、上下文长度、输出上限与温度。</div>
        </div>
        <button type="button" class="btn-outline" @click="addModel">＋ 添加模型</button>
      </div>

      <div class="provider-model-labels" aria-hidden="true">
        <span></span>
        <span>模型 ID</span>
        <span>显示名称</span>
        <span></span>
      </div>
      <div v-if="!form.models.length" class="provider-models-empty">
        还没有模型。可以点「从上游获取」，或手动添加一个。
      </div>
      <div
        v-for="(row, index) in form.models"
        :key="row.key"
        class="provider-model-row"
      >
        <button
          type="button"
          class="model-advanced-toggle"
          :aria-expanded="row.expanded"
          title="模型能力与参数"
          @click="toggleAdvanced(row)"
        >{{ row.expanded ? '⌃' : '⌄' }}</button>
        <input
          v-model="row.model"
          placeholder="例如 deepseek-chat"
          autocomplete="off"
          @change="applyModelDefaults(row)"
        />
        <input v-model="row.name" placeholder="可选" autocomplete="off" />
        <button type="button" class="btn-mini danger" @click="removeModel(index)">删除</button>
        <div v-if="row.expanded" class="model-advanced-panel">
          <div class="model-advanced-grid">
            <label class="model-check">
              <input v-model="row.supports_reasoning" type="checkbox" />
              支持扩展思考
            </label>
            <label class="model-check">
              <input v-model="row.supports_images" type="checkbox" />
              支持图片输入
            </label>
            <label class="model-advanced-field">
              <span>上下文长度</span>
              <input v-model.number="row.context_length" type="number" min="1" step="1024" />
            </label>
            <label class="model-advanced-field">
              <span>最大输出 tokens</span>
              <input v-model.number="row.max_output_tokens" type="number" min="1" step="1024" />
            </label>
            <label class="model-advanced-field">
              <span>温度</span>
              <input v-model.number="row.temperature" type="number" min="0" max="2" step="0.1" />
            </label>
            <details class="thinking-multi model-thinking">
              <summary>{{ levelsSummary(row.thinking_levels) }}</summary>
              <div class="thinking-options">
                <label v-for="option in THINKING_OPTIONS" :key="option.value">
                  <input
                    type="checkbox"
                    :checked="row.thinking_levels.includes(option.value)"
                    @change="toggleLevel(row, option.value, $event)"
                  />
                  {{ option.label }}
                </label>
              </div>
            </details>
          </div>
        </div>
      </div>
    </div>

    <p v-if="localError" class="onboarding-error">{{ localError }}</p>

    <div v-if="!hideActions" class="form-foot">
      <button type="button" class="btn-ghost" @click="emit('cancel')">取消</button>
      <button type="button" class="btn-primary" :disabled="busy" @click="submit">
        {{ busy ? '保存中…' : submitLabel }}
      </button>
    </div>
  </div>
</template>
