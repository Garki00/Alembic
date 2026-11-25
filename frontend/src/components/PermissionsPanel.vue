<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import type { PermissionBehavior, PermissionRuleInfo, PermissionRulesetInfo } from '../api/types'
import { errorMessage } from '../api/client'
import { getPermissionRules, savePermissionRules } from '../api/permissions'
import { useUiStore } from '../stores/ui'
import { useWorkspaceStore } from '../stores/workspace'

const store = useWorkspaceStore()
const ui = useUiStore()

const BEHAVIORS: Array<{ value: PermissionBehavior; title: string }> = [
  { value: 'deny', title: '拒绝' },
  { value: 'ask', title: '询问' },
  { value: 'allow', title: '允许' },
]

const ANY_TOOL = '*'
const draft = reactive<Record<PermissionBehavior, PermissionRuleInfo[]>>({
  allow: [],
  ask: [],
  deny: [],
})
const projectRules = ref<PermissionRulesetInfo | null>(null)
const loading = ref(false)
const saving = ref(false)

const toolOptions = computed(() => {
  const names = new Set<string>()
  for (const tool of store.tools) {
    for (const name of tool.tool_names) {
      if (name) names.add(name)
    }
  }
  return [...names].sort((a, b) => a.localeCompare(b))
})

function toolLabel(name: string): string {
  return name === ANY_TOOL ? '全部工具' : name
}

function rows(behavior: PermissionBehavior): PermissionRuleInfo[] {
  return draft[behavior]
}

function addRule(behavior: PermissionBehavior): void {
  draft[behavior].push({ tool: ANY_TOOL, content: '' })
}

function removeRule(behavior: PermissionBehavior, index: number): void {
  draft[behavior].splice(index, 1)
}

function visibleProjectRules(): Array<{ behavior: PermissionBehavior; rule: PermissionRuleInfo }> {
  const rules = projectRules.value?.rules
  if (!rules) return []
  return (['deny', 'ask'] as PermissionBehavior[]).flatMap((behavior) =>
    (rules[behavior] || []).map((rule) => ({ behavior, rule })),
  )
}

function applyRules(data: { rules: PermissionRulesetInfo; project_rules: PermissionRulesetInfo }): void {
  for (const behavior of ['allow', 'ask', 'deny'] as PermissionBehavior[]) {
    draft[behavior] = (data.rules?.rules?.[behavior] || []).map((rule) => ({ ...rule }))
  }
  projectRules.value = data.project_rules
}

async function load(): Promise<void> {
  loading.value = true
  try {
    if (!store.tools.length) await store.fetchTools()
    applyRules(await getPermissionRules(store.activeWorkspaceId || ''))
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  } finally {
    loading.value = false
  }
}

async function save(): Promise<void> {
  saving.value = true
  try {
    const payload = {
      allow: draft.allow.map((rule) => ({ tool: rule.tool, content: (rule.content || '').trim() })),
      ask: draft.ask.map((rule) => ({ tool: rule.tool, content: (rule.content || '').trim() })),
      deny: draft.deny.map((rule) => ({ tool: rule.tool, content: (rule.content || '').trim() })),
    }
    await savePermissionRules(payload)
    await load()
    ui.showToast('权限规则已保存')
  } catch (error) {
    ui.showToast(errorMessage(error), true)
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="tab-panel" id="tab-permissions">
    <div class="panel-head">
      <p class="muted">设置单独规则，规则先于权限模式判定。</p>
    </div>

    <div v-for="item in BEHAVIORS" :key="item.value" class="settings-group">
      <div class="settings-group-title">{{ item.title }}</div>
      <div class="general-list">
        <div v-for="(rule, index) in rows(item.value)" :key="`${item.value}-${index}`" class="rule-row">
          <select v-model="rule.tool" class="general-select rule-tool">
            <option :value="ANY_TOOL">{{ toolLabel(ANY_TOOL) }}</option>
            <option v-for="name in toolOptions" :key="name" :value="name">{{ name }}</option>
          </select>
          <input
            v-model="rule.content"
            class="rule-content"
            type="text"
            maxlength="500"
            placeholder="命令、路径或域名，可留空表示该工具的全部调用"
            autocomplete="off"
          />
          <button
            type="button"
            class="icon-btn rule-remove"
            :aria-label="`删除 ${toolLabel(rule.tool)} 规则`"
            @click="removeRule(item.value, index)"
          >
            ×
          </button>
        </div>
        <div v-if="!rows(item.value).length" class="rule-empty">
          {{ loading ? '正在读取…' : '还没有规则' }}
        </div>
        <button type="button" class="btn-mini rule-add" @click="addRule(item.value)">
          添加规则
        </button>
      </div>
    </div>

    <div class="settings-group">
      <div class="settings-group-title">项目规则</div>
      <p class="rule-hint">来自工作区 <code>.alembic/permissions.json</code></p>
      <div class="general-list">
        <div v-if="!visibleProjectRules().length" class="rule-empty">当前工作区没有项目规则</div>
        <div v-for="(entry, index) in visibleProjectRules()" :key="`project-${index}`" class="rule-project-row">
          <span class="rule-project-behavior">{{ entry.behavior === 'deny' ? '拒绝' : '询问' }}</span>
          <code>{{ entry.rule.tool }}</code>
          <span v-if="entry.rule.content" class="rule-project-content">{{ entry.rule.content }}</span>
        </div>
      </div>
    </div>

    <div class="rule-foot">
      <button type="button" class="btn-outline" :disabled="saving" @click="save">
        {{ saving ? '保存中…' : '保存规则' }}
      </button>
    </div>
  </section>
</template>
