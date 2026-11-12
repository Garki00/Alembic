<script setup lang="ts">
import type { UserAttachment } from '../types/chat'

defineProps<{ content: string; attachments?: UserAttachment[] }>()
</script>

<template>
  <div class="message user">
    <div class="bubble">
      <div v-if="content" class="bubble-text">{{ content }}</div>
      <div v-if="attachments?.length" class="message-attachments">
        <span v-for="file in attachments" :key="file.path" class="message-attachment">
          {{ file.name }}
        </span>
      </div>
    </div>
  </div>
</template>
