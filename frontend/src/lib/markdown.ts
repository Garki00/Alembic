// 轻量 Markdown 渲染（离线可用、无第三方依赖），从原 web/static/app.js 迁移。
//
// 支持：标题 h1–h6、段落、粗体/斜体/删除线、行内代码、围栏代码块、有序与无序
// 列表（可嵌套、支持任务列表）、引用、分隔线、链接、图片、GFM 表格。
// 模型输出一律先转义再拼 HTML；代码、链接这类“原子片段”先换成占位符，
// 避免强调符号误伤 URL 与代码内容。流式渲染时未闭合的代码块也照常显示。

const MD_HOLD = '\u0000'

export function escapeHtml(text: unknown): string {
  return String(text).replace(/[&<>"']/g, (char) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[char] as string))
}

export function renderMarkdown(source: unknown): string {
  if (!source) return ''
  const normalized = String(source).replace(/\u0000/g, '').replace(/\r\n?/g, '\n')
  return mdBlocks(normalized.split('\n'))
}

function mdBlocks(lines: string[], depth = 0): string {
  if (!lines.length) return ''
  if (depth > 8) return `<p>${lines.map(mdInline).join('<br>')}</p>`
  let html = ''
  let paragraph: string[] = []
  let i = 0

  const flush = () => {
    if (!paragraph.length) return
    html += `<p>${paragraph.map(mdInline).join('<br>')}</p>`
    paragraph = []
  }

  while (i < lines.length) {
    const line = lines[i]
    const text = line.trim()

    if (!text) {
      flush()
      i += 1
      continue
    }

    // 围栏代码块（``` 或 ~~~）；流式输出时没有收尾围栏也按代码块渲染
    const fence = /^(\s*)(`{3,}|~{3,})(.*)$/.exec(line)
    if (fence) {
      flush()
      const [codeHtml, next] = mdCodeBlock(lines, i, mdIndent(line))
      html += codeHtml
      i = next
      continue
    }

    const heading = /^(#{1,6})\s+(.*?)(?:\s+#+)?\s*$/.exec(text)
    if (heading) {
      flush()
      const level = heading[1].length
      html += `<h${level}>${mdInline(heading[2])}</h${level}>`
      i += 1
      continue
    }

    if (/^(?:[-*_]\s*){3,}$/.test(text)) {
      flush()
      html += '<hr>'
      i += 1
      continue
    }

    // 引用：连续以 > 开头的行合并成一个块，块内继续按 Markdown 渲染
    if (text.startsWith('>')) {
      flush()
      const inner: string[] = []
      while (i < lines.length && /^\s*>/.test(lines[i])) {
        inner.push(lines[i].replace(/^\s*>\s?/, ''))
        i += 1
      }
      html += `<blockquote>${mdBlocks(inner, depth + 1)}</blockquote>`
      continue
    }

    if (mdTableAt(lines, i)) {
      flush()
      const [table, next] = mdTable(lines, i)
      html += table
      i = next
      continue
    }

    if (mdListMarker(line)) {
      flush()
      const [list, next] = mdList(lines, i)
      html += list
      i = next
      continue
    }

    paragraph.push(text)
    i += 1
  }

  flush()
  return html
}

function mdIsFenceEnd(line: string, mark: string): boolean {
  const text = line.trim()
  return text.length >= mark.length && text.split('').every((char) => char === mark[0])
}

function mdDedent(line: string, width: number): string {
  let strip = 0
  while (strip < width && strip < line.length && (line[strip] === ' ' || line[strip] === '\t')) strip += 1
  return line.slice(strip)
}

function mdCodeBlock(lines: string[], start: number, fenceIndent = 0): [string, number] {
  const fence = /^(\s*)(`{3,}|~{3,})(.*)$/.exec(lines[start])!
  const mark = fence[2]
  const lang = fence[3].trim().split(/\s+/)[0]
  const body: string[] = []
  let i = start + 1
  while (i < lines.length && !mdIsFenceEnd(lines[i], mark)) {
    body.push(mdDedent(lines[i], fenceIndent))
    i += 1
  }
  if (i < lines.length) i += 1 // 跳过收尾围栏
  const cls = /^[\w+#.-]+$/.test(lang) ? ` class="language-${lang}"` : ''
  return [`<pre><code${cls}>${escapeHtml(body.join('\n'))}</code></pre>`, i]
}

// ---- 表格 -----------------------------------------------------------------
function mdTableAt(lines: string[], index: number): boolean {
  if (index + 1 >= lines.length) return false
  const head = lines[index]
  const separator = lines[index + 1]
  if (!head.includes('|')) return false
  const cells = mdSplitRow(separator)
  if (!cells.length || !cells.every((cell) => /^\s*:?-{1,}:?\s*$/.test(cell))) return false
  return cells.length === mdSplitRow(head).length
}

function mdSplitRow(line: string): string[] {
  let text = line.trim()
  if (text.startsWith('|')) text = text.slice(1)
  if (text.endsWith('|')) text = text.slice(0, -1)
  const cells: string[] = []
  let cell = ''
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i]
    if (char === '\\' && text[i + 1] === '|') {
      cell += '|'
      i += 1
      continue
    }
    if (char === '|') {
      cells.push(cell.trim())
      cell = ''
      continue
    }
    cell += char
  }
  cells.push(cell.trim())
  return cells
}

function mdAlign(cell: string): string {
  const value = cell.trim()
  if (/^:-+:$/.test(value)) return 'center'
  if (/^-+:$/.test(value)) return 'right'
  if (/^:-+$/.test(value)) return 'left'
  return ''
}

function mdTable(lines: string[], start: number): [string, number] {
  const header = mdSplitRow(lines[start])
  const aligns = mdSplitRow(lines[start + 1]).map(mdAlign)
  const rows: string[][] = []
  let i = start + 2
  while (i < lines.length) {
    const line = lines[i]
    if (!line.trim() || !line.includes('|')) break
    // 表格行到其它块开始时结束
    if (/^\s*(?:[-*+]|\d{1,9}[.)])\s+/.test(line) || /^#{1,6}\s/.test(line.trim())) break
    if (/^\s*>/.test(line) || /^\s*(?:`{3,}|~{3,})/.test(line)) break
    rows.push(mdSplitRow(line))
    i += 1
  }

  const cell = (tag: string, value: string, index: number) => {
    const align = aligns[index] ? ` style="text-align:${aligns[index]}"` : ''
    return `<${tag}${align}>${mdInline(value)}</${tag}>`
  }

  let html = '<div class="table-wrap"><table><thead><tr>'
  header.forEach((value, index) => { html += cell('th', value, index) })
  html += '</tr></thead>'
  if (rows.length) {
    html += '<tbody>'
    for (const row of rows) {
      html += '<tr>'
      header.forEach((_value, index) => { html += cell('td', row[index] ?? '', index) })
      html += '</tr>'
    }
    html += '</tbody>'
  }
  html += '</table></div>'
  return [html, i]
}

// ---- 列表 -----------------------------------------------------------------
function mdIndent(line: string): number {
  const match = /^\s*/.exec(line)
  return (match ? match[0] : '').replace(/\t/g, '    ').length
}

interface MdListMarker {
  indent: number
  ordered: boolean
  start: number
  text: string
}

function mdListMarker(line: string): MdListMarker | null {
  const match = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/.exec(line)
  if (!match) return null
  const text = match[3].trim()
  if (!text) return null
  return {
    indent: match[1].replace(/\t/g, '    ').length,
    ordered: /\d/.test(match[2]),
    start: parseInt(match[2], 10) || 1,
    text,
  }
}

interface MdListItem {
  parts: Array<{ type: 'text'; lines: string[] } | { type: 'html'; html: string }>
  children: string[]
}

function mdList(lines: string[], start: number): [string, number] {
  const first = mdListMarker(lines[start])!
  const baseIndent = first.indent
  const ordered = first.ordered
  const items: MdListItem[] = []
  let i = start

  const pushText = (item: MdListItem, line: string) => {
    const last = item.parts[item.parts.length - 1]
    if (last && last.type === 'text') last.lines.push(line)
    else item.parts.push({ type: 'text', lines: [line] })
  }

  while (i < lines.length) {
    const marker = mdListMarker(lines[i])
    if (marker) {
      if (marker.indent < baseIndent) break
      if (marker.indent > baseIndent) {
        if (!items.length) break
        const [child, next] = mdList(lines, i)
        items[items.length - 1].children.push(child)
        i = next
        continue
      }
      if (marker.ordered !== ordered) break
      const item: MdListItem = { parts: [], children: [] }
      pushText(item, marker.text)
      items.push(item)
      i += 1
      continue
    }

    // 空行：后面仍属于本列表时才继续，否则结束
    if (!lines[i].trim()) {
      let next = i + 1
      while (next < lines.length && !lines[next].trim()) next += 1
      if (next >= lines.length) break
      const nextMarker = mdListMarker(lines[next])
      if (nextMarker && nextMarker.indent >= baseIndent) { i = next; continue }
      if (mdIndent(lines[next]) > baseIndent) { i = next; continue }
      break
    }

    // 更深缩进且不是列表项：条目内的代码块，或该条目的续行
    if (mdIndent(lines[i]) > baseIndent && items.length) {
      if (/^\s*(`{3,}|~{3,})/.test(lines[i])) {
        const [codeHtml, next] = mdCodeBlock(lines, i, mdIndent(lines[i]))
        items[items.length - 1].parts.push({ type: 'html', html: codeHtml })
        i = next
        continue
      }
      pushText(items[items.length - 1], lines[i].trim())
      i += 1
      continue
    }
    break
  }

  const tag = ordered ? 'ol' : 'ul'
  const startAttr = ordered && first.start !== 1 ? ` start="${first.start}"` : ''
  let html = `<${tag}${startAttr}>`
  for (const item of items) {
    const chunks = item.parts.map((part) => (
      part.type === 'html' ? part.html : part.lines.map(mdInline).join('<br>')
    ))
    let className = ''
    if (chunks.length) {
      const task = /^\[([ xX])\]\s+/.exec(chunks[0])
      if (task) {
        const checked = task[1].toLowerCase() === 'x' ? ' checked' : ''
        chunks[0] = `<input type="checkbox" class="task-box" disabled${checked}> ${chunks[0].slice(task[0].length)}`
        className = ' class="task-item"'
      }
    }
    html += `<li${className}>${chunks.join('')}${item.children.join('')}</li>`
  }
  html += `</${tag}>`
  return [html, i]
}

// ---- 行内 -----------------------------------------------------------------
function mdSafeUrl(url: string, isImage: boolean): boolean {
  const value = String(url).trim()
  if (/^https?:\/\//i.test(value)) return true
  if (isImage) return false
  if (value.startsWith('//')) return false
  return /^(?:\/|\.{1,2}\/|#|mailto:)/i.test(value)
}

function mdEmphasis(text: string): string {
  return text
    .replace(/~~(?!\s)([^~\n]*[^\s~])~~/g, '<del>$1</del>')
    .replace(/\*\*(?!\s)([^*\n]*[^\s*])\*\*/g, '<strong>$1</strong>')
    .replace(/__(?!\s)([^_\n]*[^\s_])__/g, '<strong>$1</strong>')
    .replace(/(^|[^*\w])\*(?!\s)([^*\n]*[^\s*])\*(?!\*)/g, '$1<em>$2</em>')
    .replace(/(^|[^_\w])_(?!\s)([^_\n]*[^\s_])_(?![_\w])/g, '$1<em>$2</em>')
}

function looksLikeFilePath(text: unknown, requireExtension = false): boolean {
  const value = String(text || '').trim()
  if (!value || /\s/.test(value) || /^https?:\/\//i.test(value)) return false
  const base = value.replace(/\\/g, '/').split('/').pop() || ''
  const hasExtension = /^[\w.-]+\.(?:md|markdown|txt|py|js|ts|tsx|jsx|vue|json|ya?ml|toml|css|html|sh|ps1|go|rs|java|c|cpp|h|sql|ini|env|log|csv)$/i.test(base)
  if (requireExtension) return hasExtension
  if (/[\\/]/.test(value)) return true
  return hasExtension
}

function mdInline(text: string): string {
  const tokens: string[] = []
  const hold = (html: string) => `${MD_HOLD}${tokens.push(html) - 1}${MD_HOLD}`

  let out = escapeHtml(String(text))

  // 行内代码：像工作区文件路径的片段可点击预览
  out = out.replace(/`([^`\n]+)`/g, (_match, code) => {
    const file = looksLikeFilePath(code) ? ` data-file="${code}"` : ''
    return hold(`<code${file}>${code}</code>`)
  })

  // 图片
  out = out.replace(
    /!\[([^\]]*)\]\(\s*((?:[^()\s]|\([^()\s]*\))+)(?:\s+&quot;.*?&quot;)?\s*\)/g,
    (match, alt, url) => (mdSafeUrl(url, true) ? hold(`<img src="${url}" alt="${alt}" loading="lazy">`) : match),
  )

  // 链接（URL 里允许一层成对括号，例如维基百科条目）
  out = out.replace(
    /\[([^\]]*)\]\(\s*((?:[^()\s]|\([^()\s]*\))+)(?:\s+&quot;.*?&quot;)?\s*\)/g,
    (match, label, url) => {
      if (mdSafeUrl(url, false)) {
        return hold(`<a href="${url}" target="_blank" rel="noopener noreferrer">${mdEmphasis(label)}</a>`)
      }
      if (looksLikeFilePath(url)) {
        return hold(`<a href="#" data-file="${url}">${mdEmphasis(label)}</a>`)
      }
      return match
    },
  )

  // <https://…> 形式的自动链接
  out = out.replace(
    /&lt;(https?:\/\/[^\s&]+)&gt;/g,
    (_match, url) => hold(`<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`),
  )

  // 裸链接
  out = out.replace(/(^|[\s(（])(https?:\/\/[^\s<>\u0000]+)/g, (match, prefix, url) => {
    const trailing = /[.,;:!?)\]}>，。；：！？）】、]+$/.exec(url)
    const tail = trailing ? trailing[0] : ''
    const href = tail ? url.slice(0, -tail.length) : url
    if (!/^https?:\/\/[^/]/.test(href)) return match
    return `${prefix}${hold(`<a href="${href}" target="_blank" rel="noopener noreferrer">${href}</a>`)}${tail}`
  })

  // 普通文本里的工作区路径也变成可点击预览
  out = out.replace(
    /(^|[\s(（])([^\s<>"'`，。；：！？、()（）[\]【】]+)(?=$|[\s)）.,;:!?，。；：！？、])/g,
    (match, prefix, value) => {
      const trailing = /[.,;:!?]+$/.exec(value)
      const tail = trailing ? trailing[0] : ''
      const path = tail ? value.slice(0, -tail.length) : value
      if (!looksLikeFilePath(path, true)) return match
      return `${prefix}${hold(`<a href="#" data-file="${path}">${path}</a>`)}${tail}`
    },
  )

  out = mdEmphasis(out)

  for (let guard = 0; guard < 8 && out.includes(MD_HOLD); guard += 1) {
    out = out.replace(/\u0000(\d+)\u0000/g, (_match, index) => tokens[Number(index)] ?? '')
  }
  return out
}
