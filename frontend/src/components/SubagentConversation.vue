<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api/client'
import type { HistoryMessage, SubagentDetailData } from '../api/types'
import { mapHistory } from '../stores/chat'
import { useSubagentStore, type SubagentLiveStep } from '../stores/subagents'
import { createAssistantTurn, type AssistantTurn, type ChatTurn, type WorkEvent } from '../types/chat'
import AssistantMessage from './AssistantMessage.vue'
import UserMessage from './UserMessage.vue'

const props = defineProps<{ agentId: string }>()

const subagents = useSubagentStore()
const detail = ref<SubagentDetailData | null>(null)
const loading = ref(false)
// Reactive turns: the work-step rows toggle state lives on these objects.
const turns = ref<ChatTurn[]>([])
// The in-flight child turn mirrors the main conversation until it is persisted.
const liveTurn = ref<AssistantTurn | null>(null)
const running = computed(() => detail.value?.subagent.status === 'running')

type ToolWorkEvent = Extract<WorkEvent, { kind: 'tool' }>

let timer: number | undefined
let messageSignature = ''

function buildLiveEvents(steps: SubagentLiveStep[]): WorkEvent[] {
  const events: WorkEvent[] = []
  let lastThought: Extract<WorkEvent, { kind: 'thought' }> | null = null
  let lastTool: ToolWorkEvent | null = null

  steps.forEach((step, index) => {
    if (step.phase === 'thinking') {
      const text = step.text || ''
      if (!text) return
      const thoughtId = step.id || ''
      if (step.append === false) {
        // A complete thought: keep the longer version when it only repeats
        // what already streamed, otherwise show it as its own step.
        if (
          lastThought
          && (lastThought.text.endsWith(text) || text.endsWith(lastThought.text))
        ) {
          if (text.length > lastThought.text.length) lastThought.text = text
          return
        }
        lastThought = {
          kind: 'thought',
          id: thoughtId || `live-thought-${index}`,
          text,
        }
        events.push(lastThought)
        return
      }
      if (lastThought && (!thoughtId || lastThought.id === thoughtId)) {
        lastThought.text += text
      } else {
        lastThought = {
          kind: 'thought',
          id: thoughtId || `live-thought-${index}`,
          text,
        }
        events.push(lastThought)
      }
      return
    }
    lastThought = null

    if (step.phase === 'tool') {
      const event: ToolWorkEvent = {
        kind: 'tool',
        id: step.id || `live-tool-${index}`,
        name: step.name || '工具',
        args: step.args || {},
        result: '',
        error: false,
      }
      events.push(event)
      lastTool = event
      return
    }

    if (step.phase === 'tool_result') {
      let target = step.id
        ? events.find(
            (event): event is ToolWorkEvent =>
              event.kind === 'tool' && event.id === step.id,
          )
        : lastTool
      if (!target) {
        target = {
          kind: 'tool',
          id: step.id || `live-tool-${index}`,
          name: step.name || '工具',
          args: {},
          result: '',
          error: false,
        }
        events.push(target)
        lastTool = target
      }
      const content = step.content || ''
      target.result = content
      target.error = Boolean(step.error) || content.startsWith('Error:')
      return
    }

    if (step.phase === 'output' && lastTool) {
      lastTool.liveOutput = `${lastTool.liveOutput || ''}${step.text || ''}`.slice(-120_000)
    }
  })
  return events
}

function syncLiveTurn(): void {
  const id = props.agentId
  if (!running.value || !id) {
    liveTurn.value = null
    return
  }
  const turn = liveTurn.value || createAssistantTurn()
  turn.streaming = true
  turn.stepsOpen = true
  turn.startedAt = Number(detail.value?.subagent.started_at || 0) * 1000 || turn.startedAt
  turn.content = subagents.liveText[id] || ''
  turn.workEvents = buildLiveEvents(subagents.liveSteps[id] || [])
  liveTurn.value = turn
}

let liveFrame = 0

function scheduleLiveTurn(): void {
  if (liveFrame) return
  liveFrame = window.requestAnimationFrame(() => {
    liveFrame = 0
    syncLiveTurn()
  })
}

watch(
  [() => subagents.liveTick, running, () => props.agentId],
  scheduleLiveTurn,
  { immediate: true },
)

function signatureOf(messages: HistoryMessage[]): string {
  const last = messages[messages.length - 1]
  return [
    messages.length,
    last?.role || '',
    String(last?.content || '').length,
    (last?.tool_calls || []).length,
  ].join(':')
}

async function load(): Promise<void> {
  if (!props.agentId) return
  loading.value = true
  try {
    const data = await api<SubagentDetailData>(
      `/api/subagents/${encodeURIComponent(props.agentId)}`,
    )
    detail.value = data
    const messages = data.subagent.messages || []
    const signature = signatureOf(messages)
    if (signature !== messageSignature) {
      messageSignature = signature
      turns.value = mapHistory(messages)
    }
  } catch (_) {
    // The tab keeps the last snapshot; a later poll retries.
  } finally {
    loading.value = false
  }
}

function stopPolling(): void {
  if (timer !== undefined) {
    window.clearInterval(timer)
    timer = undefined
  }
}

function startPolling(): void {
  stopPolling()
  timer = window.setInterval(() => {
    if (running.value) void load()
  }, 1500)
}

watch(
  () => props.agentId,
  () => {
    detail.value = null
    turns.value = []
    liveTurn.value = null
    messageSignature = ''
    void load()
  },
)

onMounted(() => {
  void load()
  startPolling()
})

onBeforeUnmount(() => {
  stopPolling()
  if (liveFrame) {
    window.cancelAnimationFrame(liveFrame)
    liveFrame = 0
  }
})
</script>

<template>
  <div class="subagent-conversation">
    <section class="transcript subagent-transcript" aria-live="polite">
      <template v-for="turn in turns" :key="turn.id">
        <UserMessage
          v-if="turn.kind === 'user'"
          :content="turn.content"
          :attachments="turn.attachments"
          :created-at="turn.createdAt"
        />
        <AssistantMessage v-else-if="turn.kind === 'assistant'" :turn="turn" />
        <div v-else class="system-note">{{ turn.text }}</div>
      </template>
      <UserMessage
        v-if="liveTurn && subagents.livePrompt[agentId]"
        :content="subagents.livePrompt[agentId]"
        :created-at="liveTurn.startedAt"
      />
      <AssistantMessage v-if="liveTurn" :turn="liveTurn" />
      <p v-if="!turns.length && !liveTurn" class="subagent-empty">
        {{ loading ? '读取中…' : '暂无对话。' }}
      </p>
    </section>
  </div>
</template>

<style scoped>
.subagent-conversation {
  height: 100%;
  min-width: 560px;
  overflow-x: auto;
  background: var(--paper);
}

.subagent-transcript {
  min-height: 100%;
  padding-bottom: 24px;
}

.subagent-empty {
  margin: 28px auto;
  max-width: 780px;
  padding: 0 20px;
  color: var(--soft);
  font-size: 12px;
}
</style>
