<script setup lang="ts">
import { onBeforeUnmount, watch } from 'vue'
import SunIcon from './SunIcon.vue'

const props = defineProps<{
  open: boolean
  name: string
  version: string
}>()

const emit = defineEmits<{ close: [] }>()

function close(): void {
  emit('close')
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Escape' || !props.open) return
  event.preventDefault()
  close()
}

watch(
  () => props.open,
  (open) => {
    if (open) document.addEventListener('keydown', onKeydown, true)
    else document.removeEventListener('keydown', onKeydown, true)
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown, true)
})
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="about-backdrop" @click.self="close">
      <section class="about-card" role="dialog" aria-modal="true" :aria-label="name">
        <button type="button" class="about-close" aria-label="关闭" @click="close">
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path
              d="M3.2 3.2 12.8 12.8M12.8 3.2 3.2 12.8"
              fill="none"
              stroke="currentColor"
              stroke-width="1.5"
              stroke-linecap="round"
            />
          </svg>
        </button>
        <SunIcon class="about-sun" />
        <h2 class="about-name">{{ name }}</h2>
        <p class="about-version">版本 {{ version || '—' }}</p>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.about-backdrop {
  position: fixed;
  inset: 0;
  z-index: 120;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(76, 85, 73, 0.28);
  backdrop-filter: blur(3px);
  -webkit-backdrop-filter: blur(3px);
  animation: fade 140ms ease-out;
}

.about-card {
  position: relative;
  width: min(320px, 90vw);
  padding: 28px 28px 24px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--card);
  box-shadow: var(--shadow);
  text-align: center;
  animation: arrive 160ms ease-out;
}

.about-close {
  position: absolute;
  top: 10px;
  right: 10px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  padding: 0;
  border: 1px solid transparent;
  border-radius: 5px;
  background: transparent;
  color: var(--soft);
}
.about-close:hover {
  border-color: var(--border);
  background: var(--paper-deep);
  color: var(--ink);
}
.about-close svg {
  width: 14px;
  height: 14px;
}

.about-sun {
  width: 32px;
  height: 32px;
  margin: 0 auto 12px;
  color: var(--emerald);
}

.about-name {
  margin: 0;
  font-family: var(--serif);
  font-size: 22px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--ink);
}

.about-version {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--muted);
}
</style>
