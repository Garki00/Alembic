<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { PromptItem } from '../api/prompts'
import { useUiStore } from '../stores/ui'
import SettingsSheet from './SettingsSheet.vue'
import DiscardChangesDialog from './DiscardChangesDialog.vue'

type Mode = 'view' | 'create' | 'edit'

const props = defineProps<{
  mode: Mode
  prompt: PromptItem | null
  busy: boolean
}>()

const emit = defineEmits<{
  (event: 'save', name: string, content: string): void
  (event: 'close'): void
  (event: 'edit'): void
}>()

const ui = useUiStore()
const name = ref('')
const content = ref('')
const baseline = ref('')
const discardOpen = ref(false)

function snapshot(): string {
  return JSON.stringify([name.value, content.value])
}

watch(
  () => [props.mode, props.prompt] as const,
  () => {
    name.value = props.prompt?.name || ''
    content.value = props.prompt?.content || ''
    baseline.value = snapshot()
  },
  { immediate: true },
)

const dirty = computed(() => props.mode !== 'view' && snapshot() !== baseline.value)

function requestClose(): void {
  if (props.busy) return
  if (dirty.value) discardOpen.value = true
  else emit('close')
}

function discard(): void {
  discardOpen.value = false
  emit('close')
}

function submit(): void {
  if (props.busy) return
  const label = name.value.trim()
  if (!label) {
    ui.showToast('提示词名称不能为空', true)
    return
  }
  if (!content.value.trim()) {
    ui.showToast('提示词内容不能为空', true)
    return
  }
  emit('save', label, content.value)
}
</script>

<template>
  <SettingsSheet :title="mode === 'view' ? '查看提示词' : mode === 'create' ? '添加提示词' : '编辑提示词'" @close="requestClose">
    <div class="prompt-form">
      <label class="field">
        <span>名称</span>
        <input
          v-if="mode !== 'view'"
          v-model="name"
          :disabled="busy"
          autocomplete="off"
          spellcheck="false"
          placeholder="仅用于标注"
        />
        <div v-else class="prompt-static">{{ name }}</div>
      </label>
      <label class="field prompt-body-field">
        <span>提示词</span>
        <textarea
          v-if="mode !== 'view'"
          v-model="content"
          :disabled="busy"
          spellcheck="false"
          placeholder="替换身份与规则"
        ></textarea>
        <pre v-else class="prompt-static prompt-view-body">{{ content }}</pre>
      </label>
    </div>
    <template #footer>
      <button v-if="mode === 'view'" type="button" class="btn-mini" :disabled="busy" @click="emit('edit')">
        编辑
      </button>
      <button v-else type="button" class="btn-mini" :disabled="busy" @click="submit">
        {{ busy ? '保存中…' : '保存' }}
      </button>
    </template>
  </SettingsSheet>
  <DiscardChangesDialog :open="discardOpen" @cancel="discardOpen = false" @discard="discard" />
</template>

<style scoped>
.prompt-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: min(460px, 56vh);
  min-height: 260px;
}
.field { display: flex; flex-direction: column; gap: 5px; min-width: 0; font-size: 11px; color: var(--muted); }
.field > span { letter-spacing: 0.03em; }
.field input,
.field textarea,
.prompt-static {
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--card);
  color: var(--ink);
  font: inherit;
  font-size: 12.5px;
}
.field input { padding: 7px 10px; }
.field textarea {
  flex: 1;
  min-height: 0;
  padding: 9px 10px;
  resize: none;
  line-height: 1.7;
}
.field input:focus,
.field textarea:focus { outline: none; border-color: var(--accent-focus); box-shadow: 0 0 0 3px rgba(220, 239, 227, 0.7); }
.prompt-body-field { flex: 1; min-height: 0; }
.prompt-static { padding: 7px 10px; overflow-wrap: anywhere; }
.prompt-view-body {
  flex: 1;
  min-height: 0;
  margin: 0;
  overflow: auto;
  white-space: pre-wrap;
  line-height: 1.7;
  font-family: inherit;
}
</style>
