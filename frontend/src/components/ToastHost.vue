<script setup lang="ts">
import { useUiStore } from '../stores/ui'

const ui = useUiStore()
</script>

<template>
  <div v-if="ui.toastVisible" class="toast" :class="{ error: ui.toastError }" role="status">
    {{ ui.toastMessage }}
  </div>
</template>
