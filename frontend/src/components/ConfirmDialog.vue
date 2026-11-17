<script setup lang="ts">
import { ref, watch } from 'vue'

const props = defineProps<{
  open: boolean
  title: string
  message: string
}>()

const emit = defineEmits<{
  (event: 'cancel'): void
  (event: 'confirm'): void
}>()

const armed = ref(false)

watch(
  () => props.open,
  (open) => {
    if (open) armed.value = false
  },
)

function confirm(): void {
  if (!armed.value) {
    armed.value = true
    return
  }
  emit('confirm')
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="confirm-backdrop" @click.self="emit('cancel')">
      <div class="confirm-card" role="dialog" aria-modal="true">
        <div class="confirm-title">{{ title }}</div>
        <p class="confirm-message">{{ message }}</p>
        <div class="confirm-actions">
          <button type="button" class="btn-mini" @click="emit('cancel')">取消</button>
          <button type="button" class="btn-mini confirm-danger" @click="confirm">
            {{ armed ? '再次点击删除' : '删除' }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
