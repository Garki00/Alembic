<script setup lang="ts">
import { computed } from 'vue'
import type { PromptItem } from '../api/prompts'

const props = defineProps<{
  prompt: PromptItem | null
  selected: boolean
}>()

const emit = defineEmits<{
  (event: 'select'): void
  (event: 'view'): void
  (event: 'remove'): void
}>()

const builtin = computed(() => props.prompt === null)
const name = computed(() => props.prompt?.name || '默认提示词')
const content = computed(() => props.prompt?.content || '')

function choose(): void {
  if (props.selected) return
  emit('select')
}
</script>

<template>
  <article
    class="prompt-card"
    :class="{ selected, builtin }"
    :aria-label="`${name}${selected ? '（已选中）' : ''}`"
    @click="choose"
  >
    <div class="prompt-card-head">
      <h4 class="prompt-name">{{ name }}</h4>
      <button
        type="button"
        class="prompt-select"
        :class="{ selected }"
        :aria-pressed="selected"
        :aria-label="selected ? `${name} 已选中` : `使用 ${name}`"
        @click.stop="choose"
      >
        <span aria-hidden="true"></span>
      </button>
    </div>

    <p v-if="!builtin" class="prompt-excerpt">{{ content }}</p>

    <div v-if="!builtin" class="prompt-card-actions">
      <button type="button" class="btn-mini" @click.stop="emit('view')">查看</button>
      <button type="button" class="btn-mini danger" @click.stop="emit('remove')">删除</button>
    </div>
  </article>
</template>

<style scoped>
.prompt-card {
  position: relative;
  display: flex;
  flex-direction: column;
  min-width: 0;
  height: 176px;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 5px;
  background: var(--card);
  cursor: pointer;
  transition: border-color 140ms ease, background 140ms ease, box-shadow 140ms ease;
}
.prompt-card:hover { border-color: var(--accent-border); }
.prompt-card.selected {
  border-color: var(--emerald);
  box-shadow: inset 0 0 0 1px var(--emerald);
}
.prompt-card-head {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}
.prompt-name {
  flex: 1;
  min-width: 0;
  margin: 0;
  font-size: 12.5px;
  font-weight: 600;
  line-height: 1.5;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.prompt-select {
  flex: none;
  width: 18px;
  height: 18px;
  display: inline-grid;
  place-items: center;
  padding: 0;
  border: 1px solid var(--border);
  border-radius: 50%;
  background: transparent;
  transition: border-color 140ms ease, background 140ms ease;
}
.prompt-select span {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: transparent;
  transition: background 140ms ease;
}
.prompt-select.selected { border-color: var(--emerald); }
.prompt-select.selected span { background: var(--emerald); }
.prompt-card.selected:hover .prompt-select { border-color: var(--emerald); }
.prompt-excerpt {
  position: relative;
  margin: 8px 0 0;
  flex: 1;
  min-height: 0;
  overflow: hidden;
  color: var(--muted);
  font-size: 12px;
  line-height: 18.6px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 46.5px;
}
.prompt-excerpt::after {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 10px;
  background: linear-gradient(to bottom, transparent, var(--card));
  pointer-events: none;
}
.prompt-card-actions {
  display: flex;
  justify-content: flex-end;
  gap: 6px;
  margin-top: auto;
  padding-top: 10px;
}
.prompt-card-actions .btn-mini { min-width: 46px; }
</style>
