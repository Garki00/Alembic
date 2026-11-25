import type {
  CollapseTiming,
  LinkTarget,
  PermissionMode,
  TerminalKind,
  ThemeMode,
  TranscriptViewMode,
} from '../api/types'

export const THEMES: Array<{ value: ThemeMode; label: string; hint: string }> = [
  { value: 'light', label: '浅色', hint: '纸白与薄荷绿' },
  { value: 'dark', label: '深色', hint: '深绿底色 + 亮色文字' },
  { value: 'system', label: '跟随系统', hint: '随系统外观自动切换' },
]

export interface TranscriptModeOption {
  value: TranscriptViewMode
  label: string
  hint: string
}

export const TRANSCRIPT_MODES: TranscriptModeOption[] = [
  { value: 'compact', label: '简洁', hint: '只看步骤摘要' },
  { value: 'standard', label: '标准', hint: '显示工具名和结果摘要' },
  { value: 'detailed', label: '详细', hint: '显示参数和较完整结果' },
  { value: 'verbose', label: '完全展开', hint: '始终显示全部内容' },
]

export const COLLAPSE_TIMINGS: Array<{ value: CollapseTiming; label: string; hint: string }> = [
  { value: 'completion', label: '回答结束后', hint: '本轮结束后收起' },
  { value: 'next_input', label: '下次有新消息时', hint: '保留到下次发送消息' },
]

export const PERMISSION_MODES: Array<{
  value: PermissionMode
  label: string
  hint: string
}> = [
  {
    value: 'plan',
    label: '计划',
    hint: '只分析与检索，写入和命令会被拒绝',
  },
  {
    value: 'build',
    label: '构建',
    hint: '常规写入和有副作用的工具需要审批',
  },
  {
    value: 'edit',
    label: '编辑',
    hint: '工作区文件编辑免审批，命令仍需审批',
  },
  {
    value: 'yolo',
    label: '免审批',
    hint: '普通工具跳过审批，高危命令仍会拦截',
  },
]

export const TERMINALS: Array<{
  value: TerminalKind
  label: string
  hint: string
}> = [
  { value: 'powershell', label: 'PowerShell', hint: '支持管道和对象输出' },
  { value: 'cmd', label: 'CMD', hint: '兼容批处理脚本' },
  { value: 'gitbash', label: 'Git Bash', hint: '适合 POSIX 脚本' },
  { value: 'wsl', label: 'WSL', hint: '在 WSL 发行版中执行' },
]

export const LINK_TARGETS: Array<{ value: LinkTarget; label: string; hint: string }> = [
  { value: 'sidebar', label: '应用内侧边栏', hint: '在右侧边栏打开' },
  { value: 'browser', label: '默认浏览器', hint: '在系统浏览器打开' },
]

export type CompactionMode = 'default' | 'keep_first_turn'

export const COMPACTION_MODES: Array<{
  value: CompactionMode
  label: string
  hint: string
}> = [
  {
    value: 'default',
    label: '默认',
    hint: '按上下文预算压缩较早的对话',
  },
  {
    value: 'keep_first_turn',
    label: '保留第一轮',
    hint: '第一轮对话逐字保留，压缩其后的内容',
  },
]
