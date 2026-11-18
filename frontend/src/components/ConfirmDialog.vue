<script setup lang="ts">
import { ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    open: boolean
    title: string
    message: string
    /** 操作进行中：按钮禁用，避免重复提交。 */
    busy?: boolean
    /** 操作失败时的提示，显示在卡片里而不是顶部提示条。 */
    error?: string
  }>(),
  { busy: false, error: '' },
)

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
  if (props.busy) return
  if (!armed.value) {
    armed.value = true
    return
  }
  emit('confirm')
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="confirm-backdrop" @click.self="!busy && emit('cancel')">
      <div class="confirm-card" role="dialog" aria-modal="true">
        <div class="confirm-title">{{ title }}</div>
        <p class="confirm-message">{{ message }}</p>
        <p v-if="error" class="confirm-error">{{ error }}</p>
        <div class="confirm-actions">
          <button type="button" class="btn-mini" :disabled="busy" @click="emit('cancel')">
            取消
          </button>
          <button
            type="button"
            class="btn-mini confirm-danger"
            :disabled="busy"
            @click="confirm"
          >
            {{ busy ? '处理中…' : armed ? '再次点击删除' : '删除' }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
