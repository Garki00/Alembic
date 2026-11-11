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
  { value: 'compact', label: '简洁', hint: '只显示步骤摘要与错误' },
  { value: 'standard', label: '标准', hint: '显示工具名与简短结果' },
  { value: 'detailed', label: '详细', hint: '显示参数与较完整结果' },
  { value: 'verbose', label: '完全展开', hint: '始终展开全部步骤与输出' },
]

export const COLLAPSE_TIMINGS: Array<{ value: CollapseTiming; label: string; hint: string }> = [
  { value: 'completion', label: '回答结束后', hint: '本轮完成时自动收起工作步骤' },
  { value: 'next_input', label: '下次有新消息时', hint: '保留到下一次发送消息前' },
]

export const PERMISSION_MODES: Array<{
  value: PermissionMode
  label: string
  hint: string
}> = [
  {
    value: 'read_only',
    label: '只读',
    hint: '禁止写入文件与执行命令，只读工具正常使用',
  },
  {
    value: 'approval_all',
    label: '全部审批',
    hint: '所有工具调用都需要你审批',
  },
  {
    value: 'workspace_write',
    label: '工作区内修改',
    hint: '工作区内写文件免审批，执行命令仍需审批',
  },
  {
    value: 'full_access',
    label: '完全权限',
    hint: '所有工具调用都不需要审批（不可恢复的高危命令仍会被拒绝）',
  },
]

export const TERMINALS: Array<{
  value: TerminalKind
  label: string
  hint: string
}> = [
  { value: 'powershell', label: 'PowerShell', hint: 'Windows 默认终端，支持管道与对象输出' },
  { value: 'cmd', label: 'CMD', hint: '传统命令提示符，兼容批处理语法' },
  { value: 'gitbash', label: 'Git Bash', hint: 'Git for Windows 自带的 Bash，适合 POSIX 脚本' },
  { value: 'wsl', label: 'WSL', hint: '在 WSL 默认发行版里执行，工作区映射到 /mnt/<盘符>' },
]

export const LINK_TARGETS: Array<{ value: LinkTarget; label: string; hint: string }> = [
  { value: 'sidebar', label: '应用内侧边栏', hint: '在右侧边栏标签页中打开，不离开当前对话' },
  { value: 'browser', label: '默认浏览器', hint: '交给系统默认浏览器打开新标签页' },
]
