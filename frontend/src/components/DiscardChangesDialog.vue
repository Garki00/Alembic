<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ cancel: []; discard: [] }>()
const cancelButton = ref<HTMLButtonElement>()

watch(() => props.open, async open => {
  if (!open) return
  await nextTick()
  cancelButton.value?.focus()
})
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="confirm-backdrop" @click.self="emit('cancel')">
      <div class="confirm-card" role="alertdialog" aria-modal="true" aria-labelledby="discard-title">
        <div id="discard-title" class="confirm-title">未保存的修改</div>
        <p class="confirm-message">当前修改尚未保存，退出后会丢失。</p>
        <div class="confirm-actions">
          <button ref="cancelButton" type="button" class="btn-mini" @click="emit('cancel')">继续编辑</button>
          <button type="button" class="btn-mini confirm-danger" @click="emit('discard')">退出</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
