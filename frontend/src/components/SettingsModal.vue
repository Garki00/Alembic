<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'
import GeneralPanel from './GeneralPanel.vue'
import ModelsPanel from './ModelsPanel.vue'
import ToolsPanel from './ToolsPanel.vue'
import { useUiStore } from '../stores/ui'

const ui = useUiStore()

function close(): void {
  ui.closeSettings()
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') close()
}

onMounted(() => document.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div class="modal" @click.self="close">
    <div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="settings-title">
      <header class="modal-head">
        <div>
          <div class="eyebrow">SETTINGS</div>
          <h2 id="settings-title">设置</h2>
        </div>
        <button type="button" class="icon-btn" aria-label="关闭" @click="close">✕</button>
      </header>
      <div class="settings-body">
        <nav class="tabs" role="tablist">
          <button
            type="button"
            class="tab"
            :class="{ active: ui.settingsTab === 'general' }"
            role="tab"
            :aria-selected="ui.settingsTab === 'general'"
            @click="ui.settingsTab = 'general'"
          >
            通用
          </button>
          <button
            type="button"
            class="tab"
            :class="{ active: ui.settingsTab === 'models' }"
            role="tab"
            :aria-selected="ui.settingsTab === 'models'"
            @click="ui.settingsTab = 'models'"
          >
            模型
          </button>
          <button
            type="button"
            class="tab"
            :class="{ active: ui.settingsTab === 'tools' }"
            role="tab"
            :aria-selected="ui.settingsTab === 'tools'"
            @click="ui.settingsTab = 'tools'"
          >
            工具
          </button>
        </nav>

        <GeneralPanel v-if="ui.settingsTab === 'general'" />
        <ModelsPanel v-else-if="ui.settingsTab === 'models'" />
        <ToolsPanel v-else />
      </div>
    </div>
  </div>
</template>
