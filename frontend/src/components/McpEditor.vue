<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import type { McpConfig, McpServer } from '../api/extensions'
import { useUiStore } from '../stores/ui'
import { errorMessage } from '../api/client'
import SettingsSheet from './SettingsSheet.vue'
import DiscardChangesDialog from './DiscardChangesDialog.vue'

interface PairRow { key: string; value: string }

const ui = useUiStore()

const props = defineProps<{ server: McpServer | null; busy: boolean }>()
const emit = defineEmits<{ save: [id: string, config: McpConfig]; cancel: [] }>()
const form = reactive({
  name: '', enabled: true, transport: 'stdio' as McpConfig['transport'], command: '', url: '',
  args: [''] as string[],
  env: [{ key: '', value: '' }] as PairRow[],
  headers: [{ key: '', value: '' }] as PairRow[],
  clearEnv: false, clearHeaders: false, oauthEnabled: false, redirectUri: 'http://127.0.0.1:47832/callback', scope: '',
})
const baseline = ref('')
const discardOpen = ref(false)

function fingerprint(): string {
  return JSON.stringify({
    name: form.name, enabled: form.enabled, transport: form.transport, command: form.command,
    url: form.url, args: form.args, clearEnv: form.clearEnv, clearHeaders: form.clearHeaders,
    env: form.env.map(row => [row.key, row.value]), headers: form.headers.map(row => [row.key, row.value]),
    oauthEnabled: form.oauthEnabled, redirectUri: form.redirectUri, scope: form.scope,
  })
}
watch(() => props.server, server => {
  form.name = server?.name || ''
  form.enabled = server?.enabled ?? true
  form.transport = server?.transport || 'stdio'
  form.command = server?.command || ''
  form.args = server?.args?.length ? [...server.args] : ['']
  form.url = server?.url || ''
  const env = Object.entries(server?.env || {})
  form.env = env.length ? env.map(([key, value]) => ({ key, value })) : [{ key: '', value: '' }]
  const headers = Object.entries(server?.headers || {})
  form.headers = headers.length ? headers.map(([key, value]) => ({ key, value })) : [{ key: '', value: '' }]
  form.clearEnv = false
  form.clearHeaders = false
  form.oauthEnabled = !!server?.oauth
  form.redirectUri = server?.oauth?.redirect_uri || 'http://127.0.0.1:47832/callback'
  form.scope = server?.oauth?.scope || ''
  baseline.value = fingerprint()
}, { immediate: true })
const dirty = computed(() => fingerprint() !== baseline.value)

function pairs(rows: PairRow[], label: string): Record<string, string> | undefined {
  const result: Record<string, string> = {}
  for (const row of rows) {
    const key = row.key.trim()
    const value = row.value.trim()
    if (!key) {
      if (value) throw new Error(`${label}名称不能为空`)
      continue
    }
    result[key] = value
  }
  return Object.keys(result).length ? result : undefined
}
function submit(): void {
  try {
    const config: McpConfig = { name: form.name.trim(), enabled: form.enabled, transport: form.transport }
    if (!config.name) throw new Error('名称不能为空')
    if (form.transport === 'stdio') {
      if (!form.command.trim()) throw new Error('命令不能为空')
      config.command = form.command.trim()
      config.args = form.args.map(arg => arg.trim()).filter(Boolean)
      config.env = form.clearEnv ? {} : pairs(form.env, '环境变量')
      if (props.server?.oauth) config.oauth = null
    } else {
      if (!form.url.trim()) throw new Error('URL 不能为空')
      config.url = form.url.trim()
      config.headers = form.clearHeaders ? {} : pairs(form.headers, '请求头')
      config.oauth = form.oauthEnabled ? { redirect_uri: form.redirectUri.trim(), ...(form.scope.trim() ? { scope: form.scope.trim() } : {}) } : null
      if (form.oauthEnabled) {
        const redirect = new URL(form.redirectUri.trim())
        if (!['http:', 'https:'].includes(redirect.protocol)) throw new Error('回调 URL 必须使用 HTTP 或 HTTPS')
        config.timeout = 300
      }
    }
    emit('save', props.server?.id || crypto.randomUUID(), config)
  } catch (error) { ui.showToast(errorMessage(error), true) }
}
function requestClose(): void {
  if (props.busy) return
  if (dirty.value) discardOpen.value = true
  else emit('cancel')
}
function discard(): void {
  discardOpen.value = false
  emit('cancel')
}
</script>

<template>
  <SettingsSheet :title="server ? '编辑 MCP' : '添加 MCP'" @close="requestClose">
    <form id="mcp-config-form" class="mcp-form" @submit.prevent="submit">
      <label class="field"><span>名称</span><input v-model="form.name" required autocomplete="off" /></label>
      <label class="field"><span>传输方式</span><select v-model="form.transport"><option value="stdio">stdio</option><option value="streamable_http">Streamable HTTP</option><option value="sse">SSE</option></select></label>
      <template v-if="form.transport === 'stdio'">
        <label class="field"><span>命令</span><input v-model="form.command" required autocomplete="off" placeholder="node、uvx、python" /></label>
        <div class="pair-field">
          <span class="pair-label">参数</span>
          <div v-for="(_, index) in form.args" :key="index" class="pair-row single">
            <input v-model="form.args[index]" :aria-label="`参数 ${index + 1}`" autocomplete="off" spellcheck="false" />
            <button type="button" class="row-icon row-remove" aria-label="删除" @click="form.args.splice(index, 1)">
              <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.2 3.2 12.8 12.8M12.8 3.2 3.2 12.8" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" /></svg>
            </button>
          </div>
          <button type="button" class="row-add" @click="form.args.push('')">
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3.2v9.6M3.2 8h9.6" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /></svg>
            <span>添加参数</span>
          </button>
        </div>
        <div class="pair-field">
          <span class="pair-label">环境变量 <em>留空保持原值</em></span>
          <div v-for="(row, index) in form.env" :key="index" class="pair-row">
            <input v-model="row.key" :disabled="form.clearEnv" placeholder="名称" autocomplete="off" spellcheck="false" />
            <input v-model="row.value" :disabled="form.clearEnv" placeholder="值" autocomplete="off" spellcheck="false" />
            <button type="button" class="row-icon row-remove" :disabled="form.clearEnv" aria-label="删除" @click="form.env.splice(index, 1)">
              <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.2 3.2 12.8 12.8M12.8 3.2 3.2 12.8" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" /></svg>
            </button>
          </div>
          <button type="button" class="row-add" :disabled="form.clearEnv" @click="form.env.push({ key: '', value: '' })">
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3.2v9.6M3.2 8h9.6" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /></svg>
            <span>添加变量</span>
          </button>
        </div>
        <label v-if="server" class="field-clear"><input v-model="form.clearEnv" type="checkbox" /> 清除已保存的环境变量</label>
      </template>
      <template v-else>
        <label class="field"><span>URL</span><input v-model="form.url" type="url" required autocomplete="off" /></label>
        <div class="pair-field">
          <span class="pair-label">请求头 <em>留空保持原值</em></span>
          <div v-for="(row, index) in form.headers" :key="index" class="pair-row">
            <input v-model="row.key" :disabled="form.clearHeaders" placeholder="名称" autocomplete="off" spellcheck="false" />
            <input v-model="row.value" :disabled="form.clearHeaders" placeholder="值" autocomplete="off" spellcheck="false" />
            <button type="button" class="row-icon row-remove" :disabled="form.clearHeaders" aria-label="删除" @click="form.headers.splice(index, 1)">
              <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.2 3.2 12.8 12.8M12.8 3.2 3.2 12.8" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" /></svg>
            </button>
          </div>
          <button type="button" class="row-add" :disabled="form.clearHeaders" @click="form.headers.push({ key: '', value: '' })">
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3.2v9.6M3.2 8h9.6" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /></svg>
            <span>添加请求头</span>
          </button>
        </div>
        <label v-if="server" class="field-clear"><input v-model="form.clearHeaders" type="checkbox" /> 清除已保存的请求头</label>
        <label class="switch"><input v-model="form.oauthEnabled" type="checkbox" /><span class="track" />OAuth</label>
        <template v-if="form.oauthEnabled">
          <label class="field"><span>回调 URL</span><input v-model="form.redirectUri" type="url" required autocomplete="off" /></label>
          <label class="field"><span>Scope</span><input v-model="form.scope" autocomplete="off" /></label>
        </template>
      </template>
      <label class="switch"><input v-model="form.enabled" type="checkbox" /><span class="track" />已启用</label>
    </form>
    <template #footer>
      <button type="button" class="btn-mini" :disabled="busy" @click="requestClose">取消</button>
      <button type="submit" form="mcp-config-form" class="btn-mini" :disabled="busy">保存</button>
    </template>
  </SettingsSheet>
  <DiscardChangesDialog :open="discardOpen" @cancel="discardOpen = false" @discard="discard" />
</template>

<style scoped>
.mcp-form { display: grid; gap: 12px; }
.field-clear { display: flex; align-items: center; gap: 6px; color: var(--muted); font-size: 11px; }
.pair-field { display: grid; gap: 6px; }
.pair-label { font-size: 11px; color: var(--muted); letter-spacing: 0.03em; }
.pair-label em { font-style: normal; font-size: 10px; }
.pair-row { display: grid; grid-template-columns: minmax(0, 0.8fr) minmax(0, 1.2fr) auto; gap: 6px; align-items: center; }
.pair-row.single { grid-template-columns: minmax(0, 1fr) auto; }
.pair-row input {
  min-width: 0;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--card);
  padding: 6px 9px;
  font: inherit;
  font-size: 12px;
  color: var(--ink);
}
.pair-row input:focus { outline: none; border-color: var(--accent-focus); box-shadow: 0 0 0 3px rgba(220, 239, 227, 0.7); }
.row-icon {
  width: 26px;
  height: 26px;
  display: inline-grid;
  place-items: center;
  padding: 0;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--card);
  color: var(--muted);
}
.row-icon svg { width: 13px; height: 13px; }
.row-icon:hover:not(:disabled) { border-color: var(--accent-border); color: var(--emerald); }
.row-icon.row-remove:hover:not(:disabled) { border-color: var(--danger-border); color: var(--danger); background: var(--danger-bg); }
.row-icon:disabled { opacity: 0.4; cursor: default; }
.row-add {
  width: fit-content;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-top: 2px;
  padding: 3px 8px;
  border: 1px dashed var(--border);
  border-radius: 4px;
  background: transparent;
  color: var(--muted);
  font-size: 10.5px;
}
.row-add svg { width: 12px; height: 12px; }
.row-add:hover:not(:disabled) { border-color: var(--accent-border); color: var(--emerald); background: var(--surface-tint); }
.row-add:disabled { opacity: 0.5; cursor: default; }
</style>
