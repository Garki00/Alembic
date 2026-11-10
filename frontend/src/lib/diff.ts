// lib/diff.ts
// 轻量的行级 diff 与 unified diff 解析：文件查看器、变更总结和悬停预览共用。

export type DiffRowType = 'context' | 'add' | 'remove' | 'gap'

export interface DiffRow {
  type: DiffRowType
  /** 旧文件行号；新增行为 null。 */
  oldLine: number | null
  /** 新文件行号；删除行为 null。 */
  newLine: number | null
  text: string
}

export interface FileDiff {
  path: string
  before?: string
  after?: string
  /** 直接给出 unified diff 时优先解析它（apply_patch 场景）。 */
  patch?: string
  /** before/after 只是局部片段，查看器会尝试从当前文件重建完整 diff。 */
  snippet?: boolean
  /** 整文件写入，旧内容不可用。 */
  whole?: boolean
  note?: string
}

export interface PatchFile {
  path: string
  rows: DiffRow[]
  added: number
  removed: number
}

export function toLines(text: unknown): string[] {
  const value = String(text ?? '').replace(/\r\n?/g, '\n')
  if (!value) return []
  const lines = value.split('\n')
  if (lines[lines.length - 1] === '') lines.pop()
  return lines
}

function pushRow(
  rows: DiffRow[],
  type: DiffRowType,
  oldLine: number | null,
  newLine: number | null,
  text: string,
): void {
  rows.push({ type, oldLine, newLine, text })
}

/** 大文件退化：公共前缀/后缀保持上下文，中间整体视为删除 + 新增。 */
function coarseDiff(oldLines: string[], newLines: string[]): DiffRow[] {
  let prefix = 0
  while (
    prefix < oldLines.length
    && prefix < newLines.length
    && oldLines[prefix] === newLines[prefix]
  ) {
    prefix += 1
  }
  let suffix = 0
  while (
    suffix < oldLines.length - prefix
    && suffix < newLines.length - prefix
    && oldLines[oldLines.length - 1 - suffix] === newLines[newLines.length - 1 - suffix]
  ) {
    suffix += 1
  }
  const rows: DiffRow[] = []
  for (let index = 0; index < prefix; index += 1) {
    pushRow(rows, 'context', index + 1, index + 1, oldLines[index])
  }
  const oldMiddle = oldLines.slice(prefix, oldLines.length - suffix)
  const newMiddle = newLines.slice(prefix, newLines.length - suffix)
  oldMiddle.forEach((text, offset) => {
    pushRow(rows, 'remove', prefix + offset + 1, null, text)
  })
  newMiddle.forEach((text, offset) => {
    pushRow(rows, 'add', null, prefix + offset + 1, text)
  })
  for (let offset = suffix; offset > 0; offset -= 1) {
    const oldIndex = oldLines.length - offset
    const newIndex = newLines.length - offset
    pushRow(rows, 'context', oldIndex + 1, newIndex + 1, oldLines[oldIndex])
  }
  return rows
}

/** 行级 LCS diff；超过规模上限时退化为前后缀比较，避免卡死页面。 */
export function diffLines(before: unknown, after: unknown): DiffRow[] {
  const oldLines = toLines(before)
  const newLines = toLines(after)
  const n = oldLines.length
  const m = newLines.length
  if (!n) {
    return newLines.map((text, index) => ({ type: 'add' as const, oldLine: null, newLine: index + 1, text }))
  }
  if (!m) {
    return oldLines.map((text, index) => ({ type: 'remove' as const, oldLine: index + 1, newLine: null, text }))
  }
  if (n * m > 2_000_000) return coarseDiff(oldLines, newLines)

  const table: Uint32Array[] = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1))
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      table[i][j] = oldLines[i] === newLines[j]
        ? table[i + 1][j + 1] + 1
        : Math.max(table[i + 1][j], table[i][j + 1])
    }
  }

  const rows: DiffRow[] = []
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (oldLines[i] === newLines[j]) {
      pushRow(rows, 'context', i + 1, j + 1, oldLines[i])
      i += 1
      j += 1
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      pushRow(rows, 'remove', i + 1, null, oldLines[i])
      i += 1
    } else {
      pushRow(rows, 'add', null, j + 1, newLines[j])
      j += 1
    }
  }
  while (i < n) {
    pushRow(rows, 'remove', i + 1, null, oldLines[i])
    i += 1
  }
  while (j < m) {
    pushRow(rows, 'add', null, j + 1, newLines[j])
    j += 1
  }
  return rows
}

export function diffStats(rows: DiffRow[]): { added: number; removed: number } {
  let added = 0
  let removed = 0
  for (const row of rows) {
    if (row.type === 'add') added += 1
    else if (row.type === 'remove') removed += 1
  }
  return { added, removed }
}

/**
 * Collapse long unchanged runs while retaining context around each change.
 * A synthetic gap row keeps the skipped range visible without rendering it.
 */
export function collapseDiffContext(rows: DiffRow[], contextLines = 3): DiffRow[] {
  if (!rows.length) return []
  const keep = Math.max(0, contextLines)
  const collapseAbove = keep * 2 + 1
  const collapsed: DiffRow[] = []
  let index = 0

  while (index < rows.length) {
    if (rows[index].type !== 'context') {
      collapsed.push(rows[index])
      index += 1
      continue
    }

    const start = index
    while (index < rows.length && rows[index].type === 'context') index += 1
    const run = rows.slice(start, index)
    if (run.length <= collapseAbove) {
      collapsed.push(...run)
      continue
    }

    collapsed.push(...run.slice(0, keep))
    const skipped = run.length - keep * 2
    collapsed.push({
      type: 'gap',
      oldLine: null,
      newLine: null,
      text: `略过 ${skipped} 行未改动内容`,
    })
    collapsed.push(...run.slice(run.length - keep))
  }

  return collapsed
}

function cleanPatchPath(raw: string): string {
  let text = raw.split('\t', 1)[0].trim()
  if (text.length >= 2 && text.startsWith('"') && text.endsWith('"')) {
    text = text.slice(1, -1)
  }
  if (text === '/dev/null') return text
  text = text.replace(/\\/g, '/')
  if (text.startsWith('a/') || text.startsWith('b/')) text = text.slice(2)
  while (text.startsWith('./')) text = text.slice(2)
  return text
}

const HUNK_RE = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/

/** 解析 unified diff；用于把 apply_patch 的参数直接渲染成 Diff View。 */
export function parsePatch(patch: string): PatchFile[] {
  const lines = String(patch ?? '').replace(/\r\n?/g, '\n').split('\n')
  const files: PatchFile[] = []
  let index = 0
  while (index < lines.length) {
    if (!lines[index].startsWith('--- ')) {
      index += 1
      continue
    }
    if (!lines[index + 1]?.startsWith('+++ ')) break
    const oldPath = cleanPatchPath(lines[index].slice(4))
    const newPath = cleanPatchPath(lines[index + 1].slice(4))
    index += 2
    const file: PatchFile = {
      path: newPath !== '/dev/null' ? newPath : oldPath,
      rows: [],
      added: 0,
      removed: 0,
    }
    while (index < lines.length && lines[index].startsWith('@@')) {
      const match = HUNK_RE.exec(lines[index])
      if (!match) break
      let oldLine = Number(match[1])
      let newLine = Number(match[3])
      const oldCount = Number(match[2] ?? 1)
      const newCount = Number(match[4] ?? 1)
      index += 1
      let oldSeen = 0
      let newSeen = 0
      while (index < lines.length && (oldSeen < oldCount || newSeen < newCount)) {
        const line = lines[index]
        if (line.startsWith('\\')) {
          index += 1
          continue
        }
        if (line.startsWith('+')) {
          pushRow(file.rows, 'add', null, newLine, line.slice(1))
          newLine += 1
          newSeen += 1
          file.added += 1
        } else if (line.startsWith('-')) {
          pushRow(file.rows, 'remove', oldLine, null, line.slice(1))
          oldLine += 1
          oldSeen += 1
          file.removed += 1
        } else if (line.startsWith(' ')) {
          pushRow(file.rows, 'context', oldLine, newLine, line.slice(1))
          oldLine += 1
          newLine += 1
          oldSeen += 1
          newSeen += 1
        } else if (line === '') {
          pushRow(file.rows, 'context', oldLine, newLine, '')
          oldLine += 1
          newLine += 1
          oldSeen += 1
          newSeen += 1
        } else {
          break
        }
        index += 1
      }
    }
    if (file.rows.length) files.push(file)
  }
  return files
}
