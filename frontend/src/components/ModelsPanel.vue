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

const formOpen = ref(false)
const editing = ref<ProviderInfo | null>(null)
const saving = ref(false)

const confirming = ref(false)
const pending = ref<ProviderInfo | null>(null)
const removing = ref(false)
const removeError = ref('')

function providerTitle(provider: ProviderInfo): string {
  return provider.name || providerName(provider.provider)
}

function openCreate(): void {
  editing.value = null
  formOpen.value = true
}

function openEdit(provider: ProviderInfo): void {
  editing.value = provider
  formOpen.value = true
}

function closeForm(): void {
  formOpen.value = false
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

    <div class="provider-list">
      <p v-if="!store.providers.length" class="muted">
        还没有供应商，点「添加供应商」创建第一个。
      </p>
      <div
        v-for="provider in store.providers"
        :key="provider.id"
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
          <button type="button" class="btn-mini" @click="openEdit(provider)">编辑</button>
          <button type="button" class="btn-mini danger" @click="remove(provider)">删除</button>
        </div>
      </div>
    </div>

    <div v-if="formOpen" class="model-form">
      <ProviderEditor
        :key="editing?.id || 'new'"
        :initial="editing"
        :busy="saving"
        submit-label="保存供应商"
        @submit="save"
        @cancel="closeForm"
      />
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
