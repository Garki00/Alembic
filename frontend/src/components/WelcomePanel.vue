<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import type { WorkspaceInfo } from '../api/types'
import SunIcon from './SunIcon.vue'
import { useUiStore } from '../stores/ui'
import { useWorkspaceStore } from '../stores/workspace'

const workspace = useWorkspaceStore()
const ui = useUiStore()

const hasWorkspace = computed(() => Boolean(workspace.activeWorkspace))
const hasProvider = computed(() => workspace.hasProvider)
const currentPath = computed(() => workspace.activeWorkspace?.path || '')

const switcher = ref<HTMLElement | null>(null)
const menuOpen = ref(false)

function toggleMenu(): void {
  menuOpen.value = !menuOpen.value
}

function closeMenu(): void {
  menuOpen.value = false
}

function onDocumentPointer(event: PointerEvent): void {
  if (!menuOpen.value) return
  const root = switcher.value
  if (root && event.target instanceof Node && root.contains(event.target)) return
  closeMenu()
}

function onDocumentKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') closeMenu()
}

function switchWorkspace(item: WorkspaceInfo): void {
  closeMenu()
  if (item.id === workspace.activeWorkspace?.id) return
  ui.expanded[item.id] = true
  void workspace.activateWorkspace(item.id)
}

function openAddWorkspace(): void {
  closeMenu()
  ui.openWorkspaceModal()
}

function openProviderSettings(): void {
  ui.openSettings('models')
}

// 欢迎页的推荐问题先停用：位置留给「没有可用供应商」的提示。
// const suggestions = [
//   '北京现在几点？',
//   '上海今天天气怎么样？',
//   '纽约和东京现在差几个小时？',
// ]
//
// function pick(text: string): void {
//   ui.setComposerDraft(text)
// }

onMounted(() => {
  document.addEventListener('pointerdown', onDocumentPointer)
  document.addEventListener('keydown', onDocumentKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocumentPointer)
  document.removeEventListener('keydown', onDocumentKeydown)
})
</script>

<template>
  <div class="welcome">
    <div class="welcome-sun"><SunIcon /></div>
    <template v-if="hasWorkspace">
      <div class="eyebrow">{{ workspace.activeWorkspace?.name || '工作区' }}</div>
      <h1>开始对话</h1>

      <div ref="switcher" class="workspace-line">
        <span class="workspace-line-label">工作目录：</span>
        <button
          type="button"
          class="workspace-trigger"
          :class="{ open: menuOpen }"
          aria-haspopup="menu"
          :aria-expanded="menuOpen"
          :title="currentPath"
          @click="toggleMenu"
        >
          <svg class="workspace-icon" viewBox="0 0 16 16" aria-hidden="true">
            <path
              d="M1.7 4.6h4.1l1-1.3h6.5c.5 0 .9.4.9.9v6.9c0 .5-.4.9-.9.9H1.7c-.5 0-.9-.4-.9-.9V5.5c0-.5.4-.9.9-.9Z"
              fill="none"
              stroke="currentColor"
              stroke-width="1.15"
              stroke-linejoin="round"
            />
            <path d="M1.1 6.4h13.8" fill="none" stroke="currentColor" stroke-width="1" />
          </svg>
          <span class="workspace-path">{{ currentPath }}</span>
          <svg class="workspace-chevron" viewBox="0 0 16 16" aria-hidden="true">
            <path
              d="M4.6 6.4 8 9.8l3.4-3.4"
              fill="none"
              stroke="currentColor"
              stroke-width="1.35"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
        </button>

        <Transition name="workspace-menu">
          <div v-if="menuOpen" class="workspace-menu" role="menu">
            <div class="workspace-menu-head">切换工作区</div>
            <div class="workspace-menu-list">
              <button
                v-for="item in workspace.workspaces"
                :key="item.id"
                type="button"
                class="workspace-option"
                :class="{ active: item.id === workspace.activeWorkspace?.id }"
                role="menuitem"
                @click="switchWorkspace(item)"
              >
                <svg class="workspace-icon" viewBox="0 0 16 16" aria-hidden="true">
                  <path
                    d="M1.7 4.6h4.1l1-1.3h6.5c.5 0 .9.4.9.9v6.9c0 .5-.4.9-.9.9H1.7c-.5 0-.9-.4-.9-.9V5.5c0-.5.4-.9.9-.9Z"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="1.15"
                    stroke-linejoin="round"
                  />
                  <path d="M1.1 6.4h13.8" fill="none" stroke="currentColor" stroke-width="1" />
                </svg>
                <span class="workspace-option-text">
                  <span class="workspace-option-name">{{ item.name || item.path }}</span>
                  <span class="workspace-option-path">{{ item.path }}</span>
                </span>
                <svg
                  v-if="item.id === workspace.activeWorkspace?.id"
                  class="workspace-check"
                  viewBox="0 0 16 16"
                  aria-hidden="true"
                >
                  <path
                    d="M3.5 8.5 6.5 11.5 12.5 5"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="1.5"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  />
                </svg>
              </button>
            </div>
            <div class="workspace-menu-rule" aria-hidden="true"></div>
            <button type="button" class="workspace-add" role="menuitem" @click="openAddWorkspace">
              <span class="workspace-add-plus" aria-hidden="true">＋</span>
              添加工作区
            </button>
          </div>
        </Transition>
      </div>

      <div v-if="!hasProvider" class="no-provider">
        <div class="no-provider-title">当前没有可用的供应商</div>
        <p class="no-provider-text">先添加一个供应商与模型，才能开始对话。</p>
        <button type="button" class="btn-primary no-provider-action" @click="openProviderSettings">
          立刻设置
        </button>
      </div>
      <!--
        推荐问题暂时停用：需要恢复时把下面这段和 <script> 里的 suggestions / pick 一起放开。
      <div v-else class="suggestions">
        <button
          v-for="text in suggestions"
          :key="text"
          type="button"
          class="suggestion"
          @click="pick(text)"
        >
          {{ text }}
        </button>
      </div>
      -->
    </template>
    <template v-else>
      <div class="eyebrow">GET STARTED</div>
      <h1>选择一个工作区</h1>
      <p>选择或添加一个目录作为工作区，然后开始对话。</p>
      <button type="button" class="btn-primary welcome-action" @click="openAddWorkspace">
        选择工作区
      </button>
    </template>
  </div>
</template>
