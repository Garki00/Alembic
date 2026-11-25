<script setup lang="ts">
import { ref } from 'vue'
import type { ProviderInfo, ProviderPayload } from '../api/types'
import { errorMessage } from '../api/client'
import { providerName } from '../lib/providers'
import { useUiStore } from '../stores/ui'
import { useWorkspaceStore } from '../stores/workspace'
import ConfirmDialog from './ConfirmDialog.vue'
import ProviderEditor from './ProviderEditor.vue'
import ProviderIcon from './ProviderIcon.vue'

const store = useWorkspaceStore()
const ui = useUiStore()

const creating = ref(false)
const editing = ref<ProviderInfo | null>(null)
const saving = ref(false)
const activating = ref('')

const confirming = ref(false)
const pending = ref<ProviderInfo | null>(null)
const removing = ref(false)
const removeError = ref('')

function providerTitle(provider: ProviderInfo): string {
  return provider.name || providerName(provider.provider)
}

function openCreate(): void {
  editing.value = null
  creating.value = true
}

function openEdit(provider: ProviderInfo): void {
  editing.value = provider
  creating.value = false
}

async function selectProvider(provider: ProviderInfo): Promise<void> {
  const first = provider.models[0]
  if (!first || provider.id === store.activeProviderId) return
  activating.value = provider.id
  try {
    await store.activateModel(provider.id, first.id)
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  } finally {
    activating.value = ''
  }
}

function closeForm(): void {
  creating.value = false
  editing.value = null
}

async function save(payload: ProviderPayload): Promise<void> {
  saving.value = true
  try {
    await store.saveProvider(payload)
    closeForm()
    ui.showToast('供应商已保存')
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  } finally {
    saving.value = false
  }
}

function remove(provider: ProviderInfo): void {
  pending.value = provider
  removeError.value = ''
  confirming.value = true
}

function cancelRemove(): void {
  if (removing.value) return
  confirming.value = false
  pending.value = null
  removeError.value = ''
}

/** 与删除对话一致：确认卡片里完成删除，结果也回显在卡片/列表上，不再弹顶部提示。 */
async function confirmRemove(): Promise<void> {
  const provider = pending.value
  if (!provider) return
  removing.value = true
  removeError.value = ''
  try {
    await store.deleteProvider(provider.id)
    confirming.value = false
    pending.value = null
  } catch (error) {
    removeError.value = errorMessage(error)
  } finally {
    removing.value = false
  }
}
</script>

<template>
  <section class="tab-panel" id="tab-models">
    <div class="panel-head">
      <p class="muted">一个供应商可包含多个模型。</p>
      <div class="panel-actions">
        <button type="button" class="btn-outline" @click="openCreate">＋ 添加供应商</button>
      </div>
    </div>

    <div v-if="creating" class="model-form">
      <ProviderEditor
        key="new"
        :initial="null"
        :busy="saving"
        submit-label="保存供应商"
        @submit="save"
        @cancel="closeForm"
      />
    </div>

    <div class="provider-list">
      <p v-if="!store.providers.length" class="muted">
        还没有供应商，点「添加供应商」创建第一个。
      </p>
      <template v-for="provider in store.providers" :key="provider.id">
        <div
          class="provider-card"
          :class="{ active: provider.id === store.activeProviderId }"
        >
          <ProviderIcon :provider="provider.provider" :size="34" />
          <div class="provider-main">
            <div class="model-title">
              {{ providerTitle(provider) }}
              <span v-if="provider.id === store.activeProviderId" class="tag">当前</span>
              <span class="provider-type">{{ providerName(provider.provider) }}</span>
            </div>
            <div class="model-meta">
              {{ [provider.base_url,
                   provider.api_key_set ? provider.api_key_hint : '未配置 Key',
                   `${provider.models.length} 个模型`].filter(Boolean).join(' · ') }}
            </div>
          </div>
          <div class="model-actions">
            <button
              type="button"
              class="btn-mini"
              :disabled="!provider.models.length || provider.id === store.activeProviderId || activating === provider.id"
              @click="selectProvider(provider)"
            >
              选择
            </button>
            <button type="button" class="btn-mini" @click="openEdit(provider)">编辑</button>
            <button type="button" class="btn-mini danger" @click="remove(provider)">删除</button>
          </div>
        </div>
        <div v-if="editing?.id === provider.id" class="model-form">
          <ProviderEditor
            :key="editing.id"
            :initial="editing"
            :busy="saving"
            submit-label="保存供应商"
            @submit="save"
            @cancel="closeForm"
          />
        </div>
      </template>
    </div>

    <ConfirmDialog
      :open="confirming"
      title="删除供应商"
      :message="pending
        ? `删除供应商「${providerTitle(pending)}」及其 ${pending.models.length} 个模型？此操作不可恢复。`
        : ''"
      :busy="removing"
      :error="removeError"
      @cancel="cancelRemove"
      @confirm="confirmRemove"
    />
  </section>
</template>
