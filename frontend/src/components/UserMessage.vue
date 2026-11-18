<script setup lang="ts">
import { computed } from 'vue'
import { copyText } from '../lib/clipboard'
import { formatStamp } from '../lib/time'
import type { UserAttachment } from '../types/chat'

const props = defineProps<{
  content: string
  attachments?: UserAttachment[]
  createdAt?: number
}>()

const sentAtText = computed(() => formatStamp(props.createdAt))
const sentAtTitle = computed(() => {
  const ms = props.createdAt
  if (!ms) return ''
  const date = new Date(ms)
  const pad = (value: number): string => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
})

async function copyMessage(): Promise<void> {
  await copyText(props.content)
}
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

    <div class="user-meta">
      <span v-if="sentAtText" class="user-time" :title="sentAtTitle">{{ sentAtText }}</span>
      <button
        type="button"
        class="icon-action"
        title="复制消息"
        aria-label="复制消息"
        @click="copyMessage"
      >
        <svg viewBox="0 0 16 16" aria-hidden="true">
          <rect x="5.4" y="2.6" width="7.4" height="9" rx="1.2" fill="none" stroke="currentColor" stroke-width="1.2" />
          <path d="M10.4 13.4H4.4c-.7 0-1.2-.5-1.2-1.2V5.6" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" />
        </svg>
      </button>
    </div>
  </div>
</template>
