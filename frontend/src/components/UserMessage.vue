<script setup lang="ts">
defineProps<{ content: string }>()
</script>

<template>
  <div class="message user">
    <div class="bubble">{{ content }}</div>
  </div>
</template>
