<script setup lang="ts">
import { computed, nextTick, reactive, ref } from 'vue'
import type { ModelInfo } from '../api/types'
import { errorMessage } from '../api/client'
import { useUiStore } from '../stores/ui'
import { useWorkspaceStore } from '../stores/workspace'

const store = useWorkspaceStore()
const ui = useUiStore()

const formOpen = ref(false)
const editing = ref<ModelInfo | null>(null)
const saving = ref(false)
const fetching = ref(false)
const upstream = ref<string[]>([])
const nameInput = ref<HTMLInputElement | null>(null)

const form = reactive({
  name: '',
  provider: 'deepseek',
  model: '',
  base_url: 'https://api.deepseek.com',
  api_key: '',
  temperature: 0,
})

const apiKeyPlaceholder = computed(() => {
  const model = editing.value
  if (model?.api_key_set) return `${model.api_key_hint}（留空不修改）`
  return 'sk-…'
})

function resetForm(): void {
  form.name = ''
  form.provider = 'deepseek'
  form.model = ''
  form.base_url = 'https://api.deepseek.com'
  form.api_key = ''
  form.temperature = 0
}

function openCreate(): void {
  editing.value = null
  resetForm()
  upstream.value = []
  formOpen.value = true
  void nextTick(() => nameInput.value?.focus())
}

function openEdit(model: ModelInfo): void {
  editing.value = model
  form.name = model.name
  form.provider = model.provider
  form.model = model.model
  form.base_url = model.base_url || 'https://api.deepseek.com'
  form.api_key = ''
  form.temperature = model.temperature ?? 0
  formOpen.value = true
  void nextTick(() => nameInput.value?.focus())
}

function closeForm(): void {
  formOpen.value = false
  editing.value = null
}

async function submit(): Promise<void> {
  saving.value = true
  try {
    await store.saveModel({
      id: editing.value?.id ?? null,
      name: form.name,
      provider: form.provider,
      model: form.model,
      base_url: form.base_url,
      api_key: form.api_key,
      temperature: Number(form.temperature) || 0,
    })
    closeForm()
    ui.showToast('模型已保存')
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  } finally {
    saving.value = false
  }
}

async function activate(model: ModelInfo): Promise<void> {
  try {
    await store.activateModel(model.id)
    ui.showToast('已切换模型')
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  }
}

async function remove(model: ModelInfo): Promise<void> {
  if (!window.confirm(`删除模型「${model.name}」？`)) return
  try {
    await store.deleteModel(model.id)
    ui.showToast('模型已删除')
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  }
}

async function fetchUpstream(): Promise<void> {
  fetching.value = true
  try {
    const data = await store.fetchUpstreamModels({
      base_url: form.base_url.trim(),
      api_key: form.api_key.trim(),
      provider: form.provider,
    })
    upstream.value = data.fetched || []
    const added = (data.added || []).length
    const skipped = (data.skipped || []).length
    ui.showToast(added
      ? `已添加 ${added} 个模型${skipped ? `，跳过 ${skipped} 个已有模型` : ''}`
      : '上游模型都已在列表中')
    if (added) closeForm()
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  } finally {
    fetching.value = false
  }
}
</script>

<template>
  <section class="tab-panel" id="tab-models">
    <div class="panel-head">
      <p class="muted">支持 DeepSeek 与 OpenAI 兼容接口。</p>
      <div class="panel-actions">
        <button type="button" class="btn-outline" @click="openCreate">＋ 添加模型</button>
      </div>
    </div>

    <div class="model-list">
      <p v-if="!store.models.length" class="muted">
        还没有模型，点「添加模型」创建第一个。
      </p>
      <div
        v-for="model in store.models"
        :key="model.id"
        class="model-card"
        :class="{ active: model.id === store.activeModelId }"
      >
        <div class="model-main">
          <div class="model-title">
            {{ model.name }}
            <span v-if="model.id === store.activeModelId" class="tag">当前</span>
          </div>
          <div class="model-meta">
            {{ [model.provider, model.model || '(未填模型名)', model.base_url,
                 model.api_key_set ? model.api_key_hint : '未配置 Key'].filter(Boolean).join(' · ') }}
          </div>
        </div>
        <div class="model-actions">
          <button
            v-if="model.id !== store.activeModelId"
            type="button"
            class="btn-mini"
            @click="activate(model)"
          >
            设为当前
          </button>
          <button type="button" class="btn-mini" @click="openEdit(model)">编辑</button>
          <button type="button" class="btn-mini danger" @click="remove(model)">删除</button>
        </div>
      </div>
    </div>

    <form v-if="formOpen" class="model-form" @submit.prevent="submit">
      <div class="form-grid">
        <label class="field">
          <span>名称</span>
          <input ref="nameInput" v-model="form.name" placeholder="例如：DeepSeek 主模型" required />
        </label>
        <label class="field">
          <span>协议</span>
          <select v-model="form.provider">
            <option value="deepseek">DeepSeek</option>
            <option value="openai">OpenAI 兼容</option>
          </select>
        </label>
        <label class="field span-2">
          <span>模型名称</span>
          <div class="inline">
            <input v-model="form.model" list="upstream-models" placeholder="deepseek-v4-flash" />
            <button
              type="button"
              class="btn-outline"
              :disabled="fetching"
              @click="fetchUpstream"
            >
              {{ fetching ? '获取中…' : '导入全部上游模型' }}
            </button>
          </div>
          <datalist id="upstream-models">
            <option v-for="id in upstream" :key="id" :value="id"></option>
          </datalist>
        </label>
        <label class="field span-2">
          <span>Base URL</span>
          <input v-model="form.base_url" placeholder="https://api.deepseek.com" />
        </label>
        <label class="field span-2">
          <span>API Key <em class="muted">（编辑时留空表示不修改）</em></span>
          <input
            v-model="form.api_key"
            type="password"
            :placeholder="apiKeyPlaceholder"
            autocomplete="new-password"
          />
        </label>
        <label class="field">
          <span>Temperature</span>
          <input v-model.number="form.temperature" type="number" min="0" max="2" step="0.1" />
        </label>
      </div>
      <div class="form-foot">
        <button type="button" class="btn-ghost" @click="closeForm">取消</button>
        <button type="submit" class="btn-primary" :disabled="saving">
          {{ saving ? '保存中…' : '保存模型' }}
        </button>
      </div>
    </form>
  </section>
</template>
