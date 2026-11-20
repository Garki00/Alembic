<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'

defineProps<{ title: string }>()
const emit = defineEmits<{ close: [] }>()

function onKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Escape') return
  event.preventDefault()
  event.stopPropagation()
  emit('close')
}

function setBackgroundInert(value: boolean): void {
  const card = document.querySelector('.settings-card')
  if (!card) return
  for (const child of Array.from(card.children)) {
    if (!(child instanceof HTMLElement) || child.classList.contains('settings-sheet')) continue
    if (value) child.setAttribute('inert', '')
    else child.removeAttribute('inert')
  }
}

onMounted(() => {
  document.addEventListener('keydown', onKeydown, true)
  setBackgroundInert(true)
  document.querySelector<HTMLElement>('.settings-sheet-body input, .settings-sheet-body select, .settings-sheet-body textarea, .settings-sheet-body button')?.focus()
})
onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown, true)
  setBackgroundInert(false)
})
</script>

<template>
  <Teleport to=".settings-card">
    <div class="settings-sheet" @click.self="emit('close')">
      <section class="settings-sheet-card" role="dialog" aria-modal="true" :aria-label="title">
        <header class="settings-sheet-head">
          <h3>{{ title }}</h3>
          <button type="button" class="icon-btn" aria-label="关闭" @click="emit('close')">
            <svg class="icon-close" viewBox="0 0 16 16" aria-hidden="true">
              <path d="M3.2 3.2 12.8 12.8M12.8 3.2 3.2 12.8" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
            </svg>
          </button>
        </header>
        <div class="settings-sheet-body"><slot /></div>
        <footer v-if="$slots.footer" class="settings-sheet-foot"><slot name="footer" /></footer>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.settings-sheet {
  position: absolute;
  inset: 0;
  z-index: 6;
  display: grid;
  place-items: center;
  padding: 20px;
  background: rgba(76, 85, 73, 0.24);
  backdrop-filter: blur(5px);
  -webkit-backdrop-filter: blur(5px);
  animation: fade 140ms ease-out;
}
.settings-sheet-card {
  width: min(620px, 100%);
  max-height: 100%;
  display: flex;
  flex-direction: column;
  min-height: 0;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--paper);
  box-shadow: var(--shadow);
  animation: arrive 180ms ease-out;
  overflow: hidden;
}
.settings-sheet-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px 10px 18px;
  border-bottom: 1px solid var(--border);
}
.settings-sheet-head h3 { margin: 0; font-family: var(--serif); font-size: 18px; font-weight: 400; }
.settings-sheet-head .icon-btn { width: 28px; height: 28px; }
.settings-sheet-body { min-height: 0; overflow-y: auto; padding: 16px 18px; }
.settings-sheet-foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 10px 18px 14px;
  border-top: 1px solid var(--border);
}
</style>
