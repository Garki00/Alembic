<script setup lang="ts">
import { reactive, ref } from 'vue'
import type { ToolFieldInfo, ToolInfo } from '../api/types'
import { errorMessage } from '../api/client'
import { useUiStore } from '../stores/ui'
import { useWorkspaceStore } from '../stores/workspace'

const props = defineProps<{ tool: ToolInfo }>()
const store = useWorkspaceStore()
const ui = useUiStore()

const values = reactive<Record<string, string>>({})
const saving = ref(false)

for (const field of props.tool.fields) {
  values[field.key] = field.secret ? '' : field.value
}

function placeholderFor(field: ToolFieldInfo): string {
  if (field.secret && field.value_set) return '留空保持不变'
  return field.placeholder || ''
}

function span2(field: ToolFieldInfo): boolean {
  return field.kind === 'text' && field.multiline
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
  <div class="tool-card" :data-name="tool.name">
    <div class="tool-head">
      <div>
        <div class="tool-title">
          {{ tool.label }} <code>{{ tool.name }}</code>
        </div>
        <p class="tool-desc">{{ tool.description }}</p>
        <p class="tool-meta">
          <span class="risk" :class="`risk-${tool.risk}`">风险：{{ riskLabel(tool.risk) }}</span>
          <code v-for="name in tool.tool_names" :key="name" class="tool-name">{{ name }}</code>
        </p>
      </div>
      <label class="switch">
        <input type="checkbox" :checked="tool.enabled" @change="toggleEnabled" />
        <span class="track"></span>
        {{ tool.enabled ? '已启用' : '已禁用' }}
      </label>
    </div>

    <template v-if="tool.fields.length">
      <div class="tool-fields">
        <label
          v-for="field in tool.fields"
          :key="field.key"
          class="field"
          :class="{ 'span-2': span2(field) }"
        >
          <span>
            {{ field.label }}
            <em v-if="field.required" class="req" title="必填">*</em>
          </span>

          <select v-if="field.kind === 'select'" v-model="values[field.key]">
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
            @change="setSwitch(field.key, $event)"
          />

          <input
            v-else-if="field.kind === 'number'"
            v-model="values[field.key]"
            type="number"
            step="any"
            :placeholder="field.placeholder"
            autocomplete="off"
          />

          <textarea
            v-else-if="field.multiline"
            v-model="values[field.key]"
            :placeholder="placeholderFor(field)"
            autocomplete="off"
          ></textarea>

          <input
            v-else
            v-model="values[field.key]"
            :type="field.secret ? 'password' : 'text'"
            :placeholder="placeholderFor(field)"
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
        </label>
      </div>
      <div class="tool-foot">
        <button type="button" class="btn-mini" :disabled="saving" @click="save">
          {{ saving ? '保存中…' : '保存参数' }}
        </button>
      </div>
    </template>
  </div>
</template>
