<script setup lang="ts">
import { PROVIDERS } from '../lib/providers'
import ProviderIcon from './ProviderIcon.vue'

withDefaults(
  defineProps<{ modelValue: string; compact?: boolean }>(),
  { compact: false },
)

const emit = defineEmits<{ (event: 'update:modelValue', value: string): void }>()
</script>

<template>
  <div class="provider-grid" :class="{ compact }">
    <button
      v-for="provider in PROVIDERS"
      :key="provider.id"
      type="button"
      class="provider-option"
      :class="{ active: modelValue === provider.id }"
      :title="provider.hint"
      @click="emit('update:modelValue', provider.id)"
    >
      <ProviderIcon :provider="provider.id" :size="compact ? 30 : 36" />
      <span class="provider-option-text">
        <strong>{{ provider.name }}</strong>
        <small>{{ provider.short }}</small>
      </span>
    </button>
  </div>
</template>
