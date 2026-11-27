<script setup lang="ts">
import { onMounted, ref } from 'vue'
import type { PromptItem } from '../api/prompts'
import { createPrompt, deletePrompt, getPrompts, selectPrompt, updatePrompt } from '../api/prompts'
import { errorMessage } from '../api/client'
import { useUiStore } from '../stores/ui'
import ConfirmDialog from './ConfirmDialog.vue'
import PromptCard from './PromptCard.vue'
import PromptSheet from './PromptSheet.vue'

type Tab = 'prompts' | 'roles'
type SheetMode = 'view' | 'create' | 'edit'

const TABS: Array<{ id: Tab; label: string }> = [
  { id: 'prompts', label: '提示词' },
  { id: 'roles', label: '角色' },
]

const ui = useUiStore()
const tab = ref<Tab>('prompts')
const view = ref<'grid' | 'list'>('grid')
const prompts = ref<PromptItem[]>([])
const selected = ref('')
const loading = ref(false)
const loadError = ref('')
const saving = ref(false)
const sheetOpen = ref(false)
const sheetMode = ref<SheetMode>('view')
const active = ref<PromptItem | null>(null)
const deleteTarget = ref<PromptItem | null>(null)

async function load(): Promise<void> {
  loading.value = true
  loadError.value = ''
  try {
    const state = await getPrompts()
    prompts.value = state.prompts
    selected.value = state.selected
  } catch (error) {
    loadError.value = errorMessage(error)
  } finally {
    loading.value = false
  }
}

async function choose(id: string): Promise<void> {
  if (selected.value === id) return
  try {
    const state = await selectPrompt(id)
    prompts.value = state.prompts
    selected.value = state.selected
    ui.showToast('提示词已切换')
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  }
}

function startCreate(): void {
  active.value = null
  sheetMode.value = 'create'
  sheetOpen.value = true
}

function openView(prompt: PromptItem): void {
  active.value = prompt
  sheetMode.value = 'view'
  sheetOpen.value = true
}

function startEdit(): void {
  sheetMode.value = 'edit'
}

async function save(name: string, content: string): Promise<void> {
  saving.value = true
  try {
    const state = sheetMode.value === 'create'
      ? await createPrompt(name, content)
      : await updatePrompt(active.value?.id || '', name, content)
    prompts.value = state.prompts
    selected.value = state.selected
    sheetOpen.value = false
    ui.showToast('提示词已保存')
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  } finally {
    saving.value = false
  }
}

async function confirmDelete(): Promise<void> {
  const target = deleteTarget.value
  if (!target) return
  saving.value = true
  try {
    const state = await deletePrompt(target.id)
    prompts.value = state.prompts
    selected.value = state.selected
    deleteTarget.value = null
    ui.showToast('提示词已删除')
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  } finally {
    saving.value = false
  }
}

onMounted(() => load())
</script>

<template>
  <section class="tab-panel custom-panel" id="tab-custom" :aria-busy="loading">
    <div class="custom-head">
      <div class="custom-tabs" role="tablist" aria-label="自定义类型">
        <button
          v-for="item in TABS"
          :key="item.id"
          type="button"
          role="tab"
          :aria-selected="tab === item.id"
          :class="{ active: tab === item.id }"
          @click="tab = item.id"
        >
          {{ item.label }}
        </button>
      </div>
      <div v-if="tab === 'prompts'" class="view-switch" :class="{ list: view === 'list' }" role="group" aria-label="提示词视图">
        <button type="button" class="view-switch-btn" :class="{ active: view === 'grid' }" :aria-pressed="view === 'grid'" aria-label="网格视图" @click="view = 'grid'">
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 2h5v5H2zm7 0h5v5H9zM2 9h5v5H2zm7 0h5v5H9z" fill="currentColor" /></svg>
        </button>
        <button type="button" class="view-switch-btn" :class="{ active: view === 'list' }" :aria-pressed="view === 'list'" aria-label="列表视图" @click="view = 'list'">
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 3h2v2H2zm4 0h8v2H6zM2 7h2v2H2zm4 0h8v2H6zM2 11h2v2H2zm4 0h8v2H6z" fill="currentColor" /></svg>
        </button>
      </div>
    </div>

    <template v-if="tab === 'prompts'">
      <div class="prompt-toolbar">
        <button type="button" class="custom-action" :disabled="loading" @click="startCreate">
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3.2v9.6M3.2 8h9.6" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /></svg>
          <span>添加提示词</span>
        </button>
      </div>
      <p v-if="loadError" class="custom-error" role="alert">
        {{ loadError }}
        <button type="button" class="link-btn" :disabled="loading" @click="load">重试</button>
      </p>
      <div v-else class="prompt-list" :class="view">
        <PromptCard :prompt="null" :selected="selected === ''" @select="choose('')" />
        <PromptCard
          v-for="prompt in prompts"
          :key="prompt.id"
          :prompt="prompt"
          :selected="selected === prompt.id"
          @select="choose(prompt.id)"
          @view="openView(prompt)"
          @remove="deleteTarget = prompt"
        />
      </div>
    </template>
    <p v-else class="custom-empty">暂未支持...</p>

    <PromptSheet
      v-if="sheetOpen"
      :mode="sheetMode"
      :prompt="active"
      :busy="saving"
      @save="save"
      @edit="startEdit"
      @close="sheetOpen = false"
    />

    <ConfirmDialog
      :open="deleteTarget !== null"
      title="删除提示词"
      :message="`删除提示词「${deleteTarget?.name || ''}」？此操作不可恢复。`"
      :busy="saving"
      @cancel="deleteTarget = null"
      @confirm="confirmDelete"
    />
  </section>
</template>

<style scoped>
.custom-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
}
.custom-tabs { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.custom-tabs button {
  border: 1px solid transparent;
  border-radius: 5px;
  padding: 6px 12px;
  background: transparent;
  color: var(--muted);
  font-size: 12px;
}
.custom-tabs button.active { color: var(--emerald); background: var(--paper-deep); border-color: var(--border); }
.prompt-toolbar { display: flex; align-items: center; margin: 12px 0 2px; }
.custom-action {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 9px;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--card);
  color: var(--muted);
  font-size: 11px;
  line-height: 1.6;
}
.custom-action svg { width: 13px; height: 13px; flex: none; }
.custom-action:hover:not(:disabled) { border-color: var(--accent-border); color: var(--emerald); background: var(--surface-tint); }
.custom-action:disabled { opacity: 0.5; cursor: default; }
.prompt-list { display: grid; gap: 12px; margin-top: 12px; }
.prompt-list.grid { grid-template-columns: repeat(auto-fill, minmax(240px, 280px)); align-items: start; }
.prompt-list.list { grid-template-columns: minmax(0, 1fr); }
.custom-error { margin: 12px 0 0; color: var(--danger); font-size: 11px; overflow-wrap: anywhere; }
.custom-empty {
  margin: 48px 0 0;
  text-align: center;
  color: var(--soft);
  font-size: 12px;
}
</style>
