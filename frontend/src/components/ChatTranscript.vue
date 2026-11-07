<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import AssistantMessage from './AssistantMessage.vue'
import UserMessage from './UserMessage.vue'
import WelcomePanel from './WelcomePanel.vue'
import TodoPanel from './TodoPanel.vue'
import { useChatStore } from '../stores/chat'

const chat = useChatStore()
const scroller = ref<HTMLElement | null>(null)

function scrollToBottom(force = false): void {
  const element = scroller.value
  if (!element) return
  const distance = element.scrollHeight - element.scrollTop - element.clientHeight
  if (force || distance < 160) element.scrollTop = element.scrollHeight
}

watch(
  () => chat.messages.length,
  // 打开会话 / 发送消息 / 插入系统提示时都直接落到最新一条；
  // 流式增量走下面的 streamTick，用户往上翻时不会被强行拉回。
  () => { void nextTick(() => scrollToBottom(true)) },
)
watch(
  () => chat.streamTick,
  () => { void nextTick(() => scrollToBottom()) },
)
watch(
  () => chat.sessionId,
  () => { void nextTick(() => scrollToBottom(true)) },
)

onMounted(() => { void nextTick(() => scrollToBottom(true)) })
</script>

<template>
  <section ref="scroller" class="transcript" aria-live="polite">
    <TodoPanel v-if="chat.todos.length" :items="chat.todos" />
    <WelcomePanel v-if="!chat.messages.length" />
    <template v-for="turn in chat.messages" :key="turn.id">
      <UserMessage v-if="turn.kind === 'user'" :content="turn.content" />
      <AssistantMessage v-else-if="turn.kind === 'assistant'" :turn="turn" />
      <div v-else class="system-note">{{ turn.text }}</div>
    </template>
  </section>
</template>
