import type {
  CollapseTiming,
  LinkTarget,
  PermissionMode,
  TerminalKind,
  TranscriptViewMode,
} from '../api/types'

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
    value: 'read_only',
    label: '只读',
    hint: '只能读取，不能写入或执行命令',
  },
  {
    value: 'approval_all',
    label: '全部审批',
    hint: '每次工具调用都要审批',
  },
  {
    value: 'workspace_write',
    label: '工作区内修改',
    hint: '工作区内写入免审批，执行命令需审批',
  },
  {
    value: 'full_access',
    label: '完全权限',
    hint: '全部免审批，高危命令仍会拦截',
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
