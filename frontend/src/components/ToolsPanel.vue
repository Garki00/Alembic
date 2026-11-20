<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import type { ToolInfo } from '../api/types'
import { errorMessage } from '../api/client'
import { deleteMcp, getExtensions, pickDirectory, refreshExtensions, saveDirectories, saveMcp, setSkillEnabled } from '../api/extensions'
import type { ExtensionSnapshot, ExtensionSource, McpConfig, McpServer, ExtensionSkill } from '../api/extensions'
import { useChatStore } from '../stores/chat'
import { useUiStore } from '../stores/ui'
import { useWorkspaceStore } from '../stores/workspace'
import ToolCard from './ToolCard.vue'
import McpEditor from './McpEditor.vue'
import SettingsSheet from './SettingsSheet.vue'
import DiscardChangesDialog from './DiscardChangesDialog.vue'

type Tab = 'tools' | 'mcp' | 'skills'
const tabs: Array<{ id: Tab; label: string }> = [{ id: 'tools', label: '工具' }, { id: 'mcp', label: 'MCP' }, { id: 'skills', label: 'Skill' }]
const sources: Array<{ id: ExtensionSource; label: string }> = [{ id: 'builtin', label: '内置' }, { id: 'plugin', label: '插件' }]
const store = useWorkspaceStore()
const chat = useChatStore()
const ui = useUiStore()
const view = ref<'grid' | 'list'>('grid')
const tab = ref<Tab>('tools')
const snapshot = ref<ExtensionSnapshot | null>(null)
const busy = ref(false)
const picking = ref(false)
const loadError = ref('')
const directoriesOpen = ref(false)
const discardOpen = ref(false)
const directoryRows = reactive<Record<Tab, string[]>>({ tools: [''], mcp: [''], skills: [''] })
const directoryBaseline = reactive<Record<Tab, string[]>>({ tools: [], mcp: [], skills: [] })
const collapsed = reactive<Record<string, boolean>>({})
const skillDetails = reactive<Record<string, boolean>>({})
const editor = ref<McpServer | null>(null)
const editorOpen = ref(false)
const groupedTools = computed(() => store.tools.filter(tool => (tool.source || 'builtin')))

function signature(tool: ToolInfo): string {
  return `${tool.name}|${tool.fields.map(field => [field.key, field.value, field.value_set, field.hint].join(':')).join(',')}`
}
function itemsCount(source: ExtensionSource): number {
  if (tab.value === 'tools') return groupedTools.value.filter(item => (item.source || 'builtin') === source).length
  return (snapshot.value?.[tab.value] || []).filter(item => item.source === source).length
}
function normalizedRows(rows: string[]): string[] {
  return rows.map(row => row.trim()).filter(Boolean)
}
function cloneRows(paths: string[]): string[] {
  return paths.length ? [...paths] : ['']
}
function resetDirectoryDraft(data: ExtensionSnapshot): void {
  for (const item of tabs) directoryRows[item.id] = cloneRows(data.directories[item.id])
}
function rememberDirectoryBaseline(data: ExtensionSnapshot): void {
  for (const item of tabs) directoryBaseline[item.id] = normalizedRows(data.directories[item.id])
}
function applySnapshot(data: ExtensionSnapshot): void {
  snapshot.value = data
  if (!directoriesOpen.value) resetDirectoryDraft(data)
}
async function load(refresh = false): Promise<void> {
  busy.value = true
  loadError.value = ''
  try {
    applySnapshot(await (refresh ? refreshExtensions() : getExtensions()))
    await store.fetchTools()
    if (refresh) ui.showToast('插件已刷新')
  } catch (error) {
    loadError.value = errorMessage(error)
    ui.showToast(loadError.value, true)
  } finally { busy.value = false }
}
async function mutate(action: () => Promise<unknown>, message: string): Promise<boolean> {
  busy.value = true
  try {
    await action()
    applySnapshot(await getExtensions())
    await store.fetchTools()
    ui.showToast(message)
    return true
  } catch (error) {
    ui.showToast(errorMessage(error), true)
    return false
  } finally { busy.value = false }
}
const directoryDirty = computed(() => tabs.some(item => normalizedRows(directoryRows[item.id]).join('\n') !== directoryBaseline[item.id].join('\n')))
function openDirectories(): void {
  if (!snapshot.value) return
  resetDirectoryDraft(snapshot.value)
  rememberDirectoryBaseline(snapshot.value)
  directoriesOpen.value = true
}
function addDirectoryRow(kind: Tab): void {
  directoryRows[kind].push('')
}
function removeDirectoryRow(kind: Tab, index: number): void {
  directoryRows[kind].splice(index, 1)
}
async function chooseDirectory(kind: Tab, index: number): Promise<void> {
  if (picking.value) return
  picking.value = true
  try {
    const result = await pickDirectory()
    if (!result.cancelled && result.path) directoryRows[kind][index] = result.path
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  } finally {
    picking.value = false
  }
}
function requestCloseDirectories(): void {
  if (busy.value) return
  if (directoryDirty.value) discardOpen.value = true
  else directoriesOpen.value = false
}
function discardDirectories(): void {
  discardOpen.value = false
  directoriesOpen.value = false
  if (snapshot.value) resetDirectoryDraft(snapshot.value)
}
async function submitDirectories(): Promise<void> {
  const payload = {
    tools: normalizedRows(directoryRows.tools),
    mcp: normalizedRows(directoryRows.mcp),
    skills: normalizedRows(directoryRows.skills),
  }
  if (await mutate(() => saveDirectories(payload), '插件目录已保存')) directoriesOpen.value = false
}
function editServer(server: McpServer | null): void { editor.value = server; editorOpen.value = true }
async function submitServer(id: string, config: McpConfig): Promise<void> {
  if (await mutate(() => saveMcp(id, config), 'MCP 已保存')) editorOpen.value = false
}
async function removeServer(server: McpServer): Promise<void> {
  if (window.confirm(`删除 MCP「${server.name}」？`)) await mutate(() => deleteMcp(server.id), 'MCP 已删除')
}
async function toggleServer(server: McpServer, event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  if (!await mutate(() => saveMcp(server.id, { enabled: input.checked }), 'MCP 状态已保存')) input.checked = server.enabled
}
async function toggleSkill(skill: ExtensionSkill, event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  if (!await mutate(() => setSkillEnabled(skill.id, input.checked), 'Skill 状态已保存')) input.checked = skill.enabled
}
function status(server: McpServer): string {
  if (!server.enabled) return '已禁用'
  const labels: Record<string, string> = { connected: '已连接', disconnected: '未连接', connecting: '连接中', error: '连接失败', failed: '连接失败', ready: '已连接', idle: '待连接' }
  return server.status ? labels[server.status] || server.status : '状态未知'
}
async function clearApprovals(): Promise<void> {
  try { await chat.clearApprovals() } catch (error) { ui.showToast(errorMessage(error), true) }
}
onMounted(() => load())
</script>

<template>
  <section class="tab-panel extensions-panel" id="tab-tools" :aria-busy="busy">
    <div class="tools-head">
      <div class="extension-tabs" role="tablist" aria-label="扩展类型">
        <button v-for="item in tabs" :id="`extension-tab-${item.id}`" :key="item.id" type="button" role="tab" :aria-selected="tab === item.id" :aria-controls="`extension-content-${item.id}`" :class="{ active: tab === item.id }" @click="tab = item.id">{{ item.label }}</button>
      </div>
      <div class="view-switch" :class="{ list: view === 'list' }" role="group" aria-label="扩展视图">
        <button type="button" class="view-switch-btn" :class="{ active: view === 'grid' }" :aria-pressed="view === 'grid'" aria-label="网格视图" @click="view = 'grid'"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 2h5v5H2zm7 0h5v5H9zM2 9h5v5H2zm7 0h5v5H9z" fill="currentColor" /></svg></button>
        <button type="button" class="view-switch-btn" :class="{ active: view === 'list' }" :aria-pressed="view === 'list'" aria-label="列表视图" @click="view = 'list'"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 3h2v2H2zm4 0h8v2H6zM2 7h2v2H2zm4 0h8v2H6zM2 11h2v2H2zm4 0h8v2H6z" fill="currentColor" /></svg></button>
      </div>
    </div>
    <div class="extensions-toolbar">
      <button type="button" class="extensions-icon-btn" :disabled="busy" title="刷新插件" aria-label="刷新插件" @click="load(true)">
        <svg viewBox="0 0 16 16" aria-hidden="true">
          <path d="M13.2 8a5.2 5.2 0 1 1-1.6-3.7" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
          <path d="M13.4 2.8v3.1h-3.1" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>
      <div class="extensions-toolbar-right">
        <button v-if="tab === 'mcp'" type="button" class="extensions-action" :disabled="busy || !snapshot" @click="editServer(null)">
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3.2v9.6M3.2 8h9.6" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /></svg>
          <span>添加 MCP</span>
        </button>
        <button type="button" class="extensions-action" :disabled="busy || !snapshot" :aria-expanded="directoriesOpen" @click="openDirectories">
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="M1.8 5.2h4l1-1.2h6.4c.5 0 .8.3.8.8v6.4c0 .5-.3.8-.8.8H2.6c-.5 0-.8-.3-.8-.8V5.2Z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round" />
            <path d="M2.2 6.6h11.8" fill="none" stroke="currentColor" stroke-width="1.1" />
          </svg>
          <span>插件目录</span>
        </button>
      </div>
    </div>
    <SettingsSheet v-if="directoriesOpen" title="插件目录" @close="requestCloseDirectories">
      <div class="directory-groups">
        <section v-for="item in tabs" :key="item.id" class="directory-group">
          <div class="directory-label">{{ item.label }}目录</div>
          <div v-for="(_, index) in directoryRows[item.id]" :key="index" class="directory-row">
            <input v-model="directoryRows[item.id][index]" :disabled="busy" autocomplete="off" spellcheck="false" />
            <button type="button" class="row-icon" :disabled="busy || picking" aria-label="选择目录" @click="chooseDirectory(item.id, index)">
              <svg viewBox="0 0 16 16" aria-hidden="true">
                <path d="M1.8 5.2h4l1-1.2h6.4c.5 0 .8.3.8.8v6.4c0 .5-.3.8-.8.8H2.6c-.5 0-.8-.3-.8-.8V5.2Z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round" />
                <path d="M2.2 6.6h11.8" fill="none" stroke="currentColor" stroke-width="1.1" />
              </svg>
            </button>
            <button type="button" class="row-icon row-remove" :disabled="busy" aria-label="删除" @click="removeDirectoryRow(item.id, index)">
              <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.2 3.2 12.8 12.8M12.8 3.2 3.2 12.8" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" /></svg>
            </button>
          </div>
          <button type="button" class="row-add" :disabled="busy" @click="addDirectoryRow(item.id)">
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3.2v9.6M3.2 8h9.6" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /></svg>
            <span>添加目录</span>
          </button>
        </section>
      </div>
      <template #footer>
        <button type="button" class="btn-mini" :disabled="busy" @click="requestCloseDirectories">取消</button>
        <button type="button" class="btn-mini" :disabled="busy" @click="submitDirectories">保存</button>
      </template>
    </SettingsSheet>
    <DiscardChangesDialog :open="discardOpen" @cancel="discardOpen = false" @discard="discardDirectories" />
    <p v-if="loadError" class="extension-error" role="alert">{{ loadError }} <button type="button" class="link-btn" :disabled="busy" @click="load()">重试</button></p>
    <div v-if="snapshot?.errors.length" class="extension-errors" role="status"><p v-for="(error, index) in snapshot.errors" :key="index"><code>{{ error.path }}</code>：{{ error.message }}</p></div>
    <McpEditor v-if="editorOpen" :server="editor" :busy="busy" @save="submitServer" @cancel="editorOpen = false" />
    <div :id="`extension-content-${tab}`" role="tabpanel" :aria-labelledby="`extension-tab-${tab}`">
      <section v-for="source in sources" :key="`${tab}-${source.id}`" class="extension-source">
        <button class="extension-source-toggle" type="button" :aria-expanded="!collapsed[`${tab}-${source.id}`]" @click="collapsed[`${tab}-${source.id}`] = !collapsed[`${tab}-${source.id}`]">
          <span aria-hidden="true">{{ collapsed[`${tab}-${source.id}`] ? '▸' : '▾' }}</span> {{ source.label }} <span class="extension-count">{{ itemsCount(source.id) }}</span>
        </button>
        <div v-if="!collapsed[`${tab}-${source.id}`]" class="tool-list" :class="view">
          <p v-if="!itemsCount(source.id)" class="muted extension-empty">{{ busy && !snapshot ? '加载中…' : `暂无${source.label}${tabs.find(item => item.id === tab)?.label}` }}</p>
          <template v-if="tab === 'tools'"><ToolCard v-for="tool in groupedTools.filter(item => (item.source || 'builtin') === source.id)" :key="signature(tool)" :tool="tool" :view="view" /></template>
          <template v-else-if="tab === 'mcp'">
            <article v-for="server in snapshot?.mcp.filter(item => item.source === source.id) || []" :key="server.id" class="tool-card extension-card">
              <div class="tool-head"><div class="tool-title">{{ server.name }}</div><label class="switch"><input type="checkbox" :checked="server.enabled" :disabled="busy" :aria-label="`启用 ${server.name}`" @change="toggleServer(server, $event)" /><span class="track" /></label></div>
              <p class="tool-meta"><span :class="{ 'extension-error': server.error }">{{ status(server) }}</span><span>{{ server.transport }}</span><span v-if="server.tools !== undefined">{{ server.tools }} 个工具</span></p>
              <p class="extension-path">{{ server.transport === 'stdio' ? [server.command, ...(server.args || [])].join(' ') : server.url }}</p>
              <p v-if="server.error" class="extension-error">{{ server.error }}</p>
              <div class="extension-actions"><button type="button" class="btn-mini" :disabled="busy" @click="editServer(server)">编辑</button><button type="button" class="btn-mini danger" :disabled="busy" @click="removeServer(server)">删除</button></div>
            </article>
          </template>
          <template v-else>
            <article v-for="skill in snapshot?.skills.filter(item => item.source === source.id) || []" :key="skill.id" class="tool-card extension-card">
              <div class="tool-head"><div class="tool-title">{{ skill.name }}</div><label class="switch"><input type="checkbox" :checked="skill.enabled" :disabled="busy" :aria-label="`启用 ${skill.name}`" @change="toggleSkill(skill, $event)" /><span class="track" /></label></div>
              <p class="tool-desc">{{ skill.description }}</p><p v-if="skill.error" class="extension-error">{{ skill.error }}</p>
              <div class="extension-actions"><span class="tool-state">{{ skill.enabled ? '已启用' : '已禁用' }}</span><button type="button" class="link-btn" :aria-expanded="!!skillDetails[skill.id]" @click="skillDetails[skill.id] = !skillDetails[skill.id]">{{ skillDetails[skill.id] ? '收起详情' : '详情' }}</button></div>
              <dl v-if="skillDetails[skill.id]" class="extension-detail"><dt>路径</dt><dd>{{ skill.path }}</dd><dt v-if="skill.license">许可证</dt><dd v-if="skill.license">{{ skill.license }}</dd><dt v-if="skill.compatibility">兼容性</dt><dd v-if="skill.compatibility">{{ skill.compatibility }}</dd><dt>ID</dt><dd>{{ skill.id }}</dd></dl>
            </article>
          </template>
        </div>
      </section>
    </div>
    <div v-if="tab === 'tools'" class="tool-foot"><button type="button" class="btn-mini danger" @click="clearApprovals">清除本会话「始终允许」</button></div>
  </section>
</template>

<style scoped>
.extension-tabs, .extension-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.extension-tabs button { border: 1px solid transparent; border-radius: 5px; padding: 6px 12px; background: transparent; color: var(--muted); font-size: 12px; }
.extension-tabs button.active { color: var(--emerald); background: var(--paper-deep); border-color: var(--border); }
.extension-actions { margin-top: 12px; }
.extensions-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 12px; }
.extensions-toolbar-right { display: flex; align-items: center; gap: 8px; }
.extensions-icon-btn {
  width: 28px;
  height: 28px;
  display: inline-grid;
  place-items: center;
  padding: 0;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--muted);
}
.extensions-icon-btn svg { width: 14px; height: 14px; }
.extensions-icon-btn:hover:not(:disabled) { color: var(--emerald); }
.extensions-action {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 9px;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--card);
  color: var(--muted);
  font-size: 11px;
  line-height: 1.6;
}
.extensions-action svg { width: 13px; height: 13px; flex: none; }
.extensions-action:hover:not(:disabled) { border-color: var(--accent-border); color: var(--emerald); background: var(--surface-tint); }
.extensions-icon-btn:disabled, .extensions-action:disabled { opacity: 0.5; cursor: default; }
.extension-source { margin-top: 18px; }
.extension-source-toggle { display: flex; align-items: center; gap: 8px; width: 100%; border: 0; border-bottom: 1px solid var(--border-soft); padding: 8px 0; background: transparent; color: var(--ink); text-align: left; font-size: 12px; }
.extension-count { color: var(--soft); font-size: 11px; }
.extension-empty { margin: 0; grid-column: 1 / -1; }
.extension-card { min-width: 0; }
.extension-card .tool-title { overflow-wrap: anywhere; }
.extension-path, .extension-detail { font-size: 11px; color: var(--muted); overflow-wrap: anywhere; }
.extension-path { font-family: var(--mono); margin: 8px 0; }
.extension-error, .extension-errors { color: var(--danger); font-size: 11px; overflow-wrap: anywhere; }
.extension-errors { padding: 0 10px; border: 1px solid var(--danger-border); border-radius: 5px; margin-top: 12px; background: var(--danger-bg); }
.extension-detail { border-top: 1px solid var(--border-soft); padding-top: 10px; margin-bottom: 0; }
.extension-detail dt { color: var(--soft); margin-top: 6px; }
.extension-detail dd { margin: 2px 0 0; }
.directory-groups { display: grid; gap: 18px; }
.directory-group { display: grid; gap: 6px; }
.directory-label { font-size: 11px; color: var(--muted); letter-spacing: 0.03em; }
.directory-row { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: 6px; align-items: center; }
.directory-row input {
  min-width: 0;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--card);
  padding: 6px 9px;
  font: 12px var(--mono);
  color: var(--ink);
}
.directory-row input:focus { outline: none; border-color: var(--accent-focus); box-shadow: 0 0 0 3px rgba(220, 239, 227, 0.7); }
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
