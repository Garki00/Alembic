<script setup lang="ts">
import { reactive, ref } from 'vue'
import type { ToolFieldInfo, ToolInfo } from '../api/types'
import { errorMessage } from '../api/client'
import { useUiStore } from '../stores/ui'
import { useWorkspaceStore } from '../stores/workspace'

const props = withDefaults(
  defineProps<{ tool: ToolInfo; view?: 'grid' | 'list' }>(),
  { view: 'grid' },
)
const store = useWorkspaceStore()
const ui = useUiStore()

const values = reactive<Record<string, string>>({})
const saving = ref(false)
const expanded = ref(false)

for (const field of props.tool.fields) {
  values[field.key] = field.secret ? '' : field.value
}

function placeholderFor(field: ToolFieldInfo): string {
  if (field.secret && field.value_set) return '留空保持不变'
  return field.placeholder || ''
}

function span2(field: ToolFieldInfo): boolean {
  return field.kind === 'radio' || (field.kind === 'text' && field.multiline)
}

function fieldDisabled(field: ToolFieldInfo): boolean {
  const rule = field.enabled_when
  if (!rule) return false
  return (values[rule.key] ?? '') !== rule.value
}

function riskLabel(risk: string): string {
  if (risk === 'exec') return '执行命令'
  if (risk === 'write') return '修改文件'
  return '只读'
}

function setSwitch(key: string, event: Event): void {
  const input = event.target as HTMLInputElement
  values[key] = input.checked ? 'true' : 'false'
}

function toggleExpanded(): void {
  expanded.value = !expanded.value
}

function onCardClick(event: MouseEvent): void {
  if (props.view !== 'grid') return
  const target = event.target as HTMLElement | null
  if (target?.closest('button, input, select, textarea, label, a, details, summary')) return
  toggleExpanded()
}

async function toggleEnabled(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const enabled = input.checked
  try {
    await store.updateTool(props.tool.name, { enabled })
    ui.showToast(enabled ? '工具已启用' : '工具已禁用')
  } catch (error) {
    input.checked = props.tool.enabled
    ui.showToast(errorMessage(error), true)
  }
}

async function save(): Promise<void> {
  const config: Record<string, string> = {}
  for (const field of props.tool.fields) {
    config[field.key] = values[field.key] ?? ''
  }
  saving.value = true
  try {
    await store.updateTool(props.tool.name, { config })
    ui.showToast('工具参数已保存')
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  } finally {
    saving.value = false
  }
}

async function clearSecret(field: ToolFieldInfo): Promise<void> {
  if (!window.confirm(`清除 ${field.key}？清除后需要重新填写，相关工具才能继续使用。`)) return
  try {
    await store.updateTool(props.tool.name, { clear: [field.key] })
    ui.showToast('已清除')
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  }
}
</script>

<template>
  <div
    class="tool-card"
    :class="[`tool-card-${view}`, { expanded }]"
    :data-name="tool.name"
    @click="onCardClick"
  >
    <button
      v-if="view === 'grid'"
      type="button"
      class="tool-expand"
      :aria-expanded="expanded"
      :title="expanded ? '收起设置' : '展开设置'"
      @click.stop="toggleExpanded"
    >
      <svg
        class="tool-expand-icon"
        :class="{ open: expanded }"
        viewBox="0 0 16 16"
        aria-hidden="true"
      >
        <path
          d="M4.5 6.5 8 10l3.5-3.5"
          fill="none"
          stroke="currentColor"
          stroke-width="1.5"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
      </svg>
    </button>

    <div class="tool-head">
      <div>
        <div class="tool-title">
          {{ tool.label }} <code>{{ tool.name }}</code>
        </div>
        <p class="tool-desc">{{ tool.description }}</p>
        <p class="tool-meta">
          <span class="risk" :class="`risk-${tool.risk}`">风险：{{ riskLabel(tool.risk) }}</span>
          <template v-if="view === 'list'">
            <code v-for="name in tool.tool_names" :key="name" class="tool-name">{{ name }}</code>
          </template>
        </p>
      </div>
      <label v-if="view === 'list'" class="switch" @click.stop>
        <input type="checkbox" :checked="tool.enabled" @change="toggleEnabled" />
        <span class="track"></span>
        {{ tool.enabled ? '已启用' : '已禁用' }}
      </label>
    </div>

    <div v-if="view === 'grid'" class="tool-grid-foot">
      <span class="tool-state">{{ tool.enabled ? '已启用' : '已禁用' }}</span>
      <label class="switch" @click.stop>
        <input type="checkbox" :checked="tool.enabled" @change="toggleEnabled" />
        <span class="track"></span>
      </label>
    </div>

    <div v-if="view === 'list' || expanded" class="tool-details" @click.stop>
      <div v-if="view === 'grid'" class="tool-includes">
        <span class="tool-includes-label">包含工具</span>
        <code v-for="name in tool.tool_names" :key="name" class="tool-name">{{ name }}</code>
      </div>
      <template v-if="tool.fields.length">
        <div class="tool-fields">
          <component
            :is="field.kind === 'radio' ? 'div' : 'label'"
            v-for="field in tool.fields"
            :key="field.key"
            class="field"
            :class="{ 'span-2': span2(field), 'field-muted': fieldDisabled(field) }"
          >
            <span>
              {{ field.label }}
              <em v-if="field.required" class="req" title="必填">*</em>
            </span>

            <div v-if="field.kind === 'radio'" class="field-radio" role="radiogroup">
              <label
                v-for="option in field.options"
                :key="option.value"
                class="radio-option"
                :class="{ active: values[field.key] === option.value }"
              >
                <input
                  v-model="values[field.key]"
                  type="radio"
                  :name="`tool-${tool.name}-${field.key}`"
                  :value="option.value"
                  :disabled="fieldDisabled(field)"
                />
                <span>{{ option.label }}</span>
              </label>
            </div>

            <select
              v-else-if="field.kind === 'select'"
              v-model="values[field.key]"
              :disabled="fieldDisabled(field)"
            >
              <option value="">（未设置）</option>
              <option
                v-for="option in field.options"
                :key="option.value"
                :value="option.value"
              >
                {{ option.label }}
              </option>
            </select>

            <input
              v-else-if="field.kind === 'switch'"
              type="checkbox"
              class="field-switch"
              :checked="values[field.key] === 'true'"
              :disabled="fieldDisabled(field)"
              @change="setSwitch(field.key, $event)"
            />

            <input
              v-else-if="field.kind === 'number'"
              v-model="values[field.key]"
              type="number"
              step="any"
              :placeholder="field.placeholder"
              :disabled="fieldDisabled(field)"
              autocomplete="off"
            />

            <textarea
              v-else-if="field.multiline"
              v-model="values[field.key]"
              :placeholder="placeholderFor(field)"
              :disabled="fieldDisabled(field)"
              autocomplete="off"
            ></textarea>

            <input
              v-else
              v-model="values[field.key]"
              :type="field.secret ? 'password' : 'text'"
              :placeholder="placeholderFor(field)"
              :disabled="fieldDisabled(field)"
              autocomplete="off"
            />

            <span
              v-if="field.secret"
              class="field-status"
              :class="{ empty: !field.value_set }"
            >
              {{ field.value_set ? `已配置 · ${field.hint}` : '未配置' }}
              <button
                v-if="field.value_set"
                type="button"
                class="link-btn"
                @click.prevent="clearSecret(field)"
              >
                清除
              </button>
            </span>
          </component>
        </div>
        <div class="tool-foot">
          <button type="button" class="btn-mini" :disabled="saving" @click="save">
            {{ saving ? '保存中…' : '保存参数' }}
          </button>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.field-muted { opacity: 0.45; }
.field-muted :disabled { cursor: not-allowed; }
.field-radio { display: flex; flex-wrap: wrap; gap: 8px; }
.radio-option {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 28px;
  padding: 3px 10px;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--card);
  color: var(--muted);
  font-size: 12px;
  cursor: pointer;
}
.radio-option:hover { border-color: var(--accent-border); color: var(--ink); }
.radio-option.active { border-color: var(--accent-focus); background: var(--surface-tint); color: var(--emerald); }
.field-radio input[type='radio'] {
  width: 14px;
  height: 14px;
  margin: 0;
  padding: 0;
  border: 0;
  background: none;
  accent-color: var(--emerald);
}
</style>
