<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ChatQuestion } from '../types/chat'

const props = defineProps<{ question: ChatQuestion }>()
const emit = defineEmits<{ answer: [value: string] }>()

const text = ref('')
const resolved = computed(() => props.question.status !== 'pending')
const canSubmit = computed(
  () => !resolved.value && !props.question.submitting && text.value.trim().length > 0,
)

function submit(value: string): void {
  if (resolved.value || props.question.submitting) return
  const clean = value.trim()
  if (!clean && !props.question.allow_free_text) return
  emit('answer', clean)
}
</script>

<template>
  <div class="question-card" :class="{ resolved }" :data-question-id="question.id">
    <div class="question-head">
      <span class="question-title">需要你的输入</span>
    </div>
    <div class="question-text">{{ question.question }}</div>

    <template v-if="!resolved">
      <div v-if="question.options.length" class="question-options">
        <button
          v-for="option in question.options"
          :key="option"
          type="button"
          class="btn-mini"
          :disabled="question.submitting"
          @click="submit(option)"
        >
          {{ option }}
        </button>
      </div>
      <div v-if="question.allow_free_text" class="question-free">
        <textarea
          v-model="text"
          rows="2"
          placeholder="输入回答，Enter 提交"
          :disabled="question.submitting"
          @keydown.enter.exact.prevent="submit(text)"
        ></textarea>
        <button
          type="button"
          class="btn-mini primary"
          :disabled="!canSubmit"
          @click="submit(text)"
        >
          提交
        </button>
      </div>
    </template>

    <div v-else-if="question.status === 'answered'" class="question-result">
      已回答：{{ question.answer || '（空）' }}
    </div>
    <div v-else class="question-result">
      等待超时，模型将按自己的假设继续
    </div>
  </div>
</template>
