# tools/command_brief.py
"""把一条 shell 命令翻译成人能一眼看懂的中文说明。

审批卡片要回答的是「它到底要做什么」，而不是把命令原文再贴一遍。命令原文
可能是一条十几段的 PowerShell 管道，用户很难在几秒内判断该不该点「允许」。

这里的做法是**离线规则**，不额外调用模型：

- 按 ``;`` / 换行 / ``&&`` / ``||`` 拆成语句，再按 ``|`` 拆成管道段；
- 识别常见的 PowerShell / CMD / bash / 构建工具动词（列目录、搜索、写文件、
  删除、跑测试、Git 操作等），翻译成一句短语；
- 管道段按顺序串起来（「列出 *.html 文件，然后只保留 Name、Length 两列」），
  语句之间用「；」分隔；
- 看不懂的命令退回「运行 xxx 命令」，绝不编造意图。

输出只用于审批展示与「始终允许」的说明文字，不参与命令执行，因此规则
失效时最坏结果只是描述变粗，不会改变真实执行内容。
"""

from __future__ import annotations

import re

#: 说明最多保留几条语句；太多只说明用户也不会读。
MAX_STEPS = 6
#: 单句说明的字符上限。
MAX_SUMMARY = 180

#: 正则里的元字符：把 pattern 还原成「关键字」时去掉。
_REGEX_NOISE = re.compile(r"[\\()\[\]{}+*?^$|]")
#: 只有装饰符号的文本（如 ``----``）不值得写进说明。
_SEPARATOR_TEXT = re.compile(r"^[\s\-=#*_~·—─]+$")
#: 变量赋值：``$f = ...`` / ``Exists=(...)``。
_ASSIGNMENT = re.compile(r"^(?P<name>\$?[\w.\[\]:]+)\s*=(?!=)\s*(?P<rhs>.+)$", re.S)

_QUOTES = "\"'`"
_BRACKETS = {"(": ")", "[": "]", "{": "}"}

#: 表达式里出现这些调用时，按短语描述（只取第一个就能读懂整体意图）。
_EXPRESSION_PHRASES = (
    (re.compile(r"(?i)\btest-path\b"), "检查路径是否存在"),
    (re.compile(r"(?i)\bget-content\b"), "读取文件内容"),
    (re.compile(r"(?i)\b(get-childitem|gci)\b"), "列出文件"),
    (re.compile(r"(?i)\b(get-item|gi)\b"), "查看文件信息"),
    (re.compile(r"(?i)\b(select-string|sls|findstr)\b"), "搜索文本"),
    (re.compile(r"(?i)\b(invoke-webrequest|iwr|curl|wget)\b"), "请求网络地址"),
)

#: Git 子命令 → 短语。
_GIT_VERBS = {
    "status": "查看 Git 状态",
    "diff": "查看改动差异",
    "log": "查看提交历史",
    "show": "查看某次提交",
    "add": "暂存改动",
    "commit": "提交改动",
    "push": "推送提交到远端",
    "pull": "拉取远端更新",
    "fetch": "获取远端更新",
    "clone": "克隆仓库",
    "init": "初始化 Git 仓库",
    "branch": "查看或管理分支",
    "checkout": "切换分支或恢复文件",
    "switch": "切换分支",
    "merge": "合并分支",
    "rebase": "变基整理提交",
    "reset": "重置提交或工作区",
    "clean": "清理未跟踪文件",
    "stash": "暂存或恢复工作区改动",
    "remote": "管理远端地址",
    "tag": "查看或打标签",
}

#: npm / pnpm / yarn 子命令 → 短语。
_PACKAGE_SCRIPTS = {
    "install": "安装依赖",
    "i": "安装依赖",
    "ci": "安装依赖",
    "add": "安装依赖包",
    "remove": "卸载依赖包",
    "uninstall": "卸载依赖包",
    "run": "运行脚本",
    "test": "运行测试",
    "build": "构建项目",
    "start": "启动服务",
    "dev": "启动开发服务",
    "lint": "检查代码风格",
    "publish": "发布包到仓库",
    "update": "更新依赖",
}


def _clip(text: str, limit: int = MAX_SUMMARY) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def _split_top_level(text: str, separators: tuple[str, ...]) -> list[str]:
    """按顶层分隔符切分，忽略引号、括号、脚本块内部的符号。"""
    parts: list[str] = []
    buf: list[str] = []
    stack: list[str] = []
    quote = ""
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            buf.append(char)
            if char == quote:
                # PowerShell 用两个单引号转义单引号。
                if quote == "'" and index + 1 < len(text) and text[index + 1] == "'":
                    buf.append(text[index + 1])
                    index += 2
                    continue
                quote = ""
            index += 1
            continue
        if char in _QUOTES:
            quote = char
            buf.append(char)
            index += 1
            continue
        if char in _BRACKETS:
            stack.append(_BRACKETS[char])
        elif stack and char == stack[-1]:
            stack.pop()
        if not stack:
            matched = next((sep for sep in separators if text.startswith(sep, index)), None)
            if matched:
                parts.append("".join(buf))
                buf = []
                index += len(matched)
                continue
        buf.append(char)
        index += 1
    parts.append("".join(buf))
    return parts


def _balanced(text: str) -> bool:
    """括号/引号是否配对（用于剥掉一层 ``( ... )``）。"""
    stack: list[str] = []
    quote = ""
    for char in text:
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in _QUOTES:
            quote = char
        elif char in _BRACKETS:
            stack.append(_BRACKETS[char])
        elif stack and char == stack[-1]:
            stack.pop()
        elif char in _BRACKETS.values():
            return False
    return not stack and not quote


def _unquote(text: str) -> str:
    text = str(text or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in _QUOTES:
        return text[1:-1].strip()
    return text


def _first_token(text: str) -> tuple[str, str]:
    """返回 ``(命令名, 其余参数)``；命令名去掉引号与常见前缀。"""
    stripped = text.strip()
    stripped = re.sub(r"^(?:&\s*|call\s+|sudo\s+)", "", stripped)
    match = re.match(r"(\"[^\"]+\"|'[^']+'|\S+)", stripped)
    if not match:
        return "", stripped
    token = _unquote(match.group(1))
    return token, stripped[match.end():].strip()


def _program_key(token: str) -> str:
    """命令名 → 便于比对的小写短名（去掉路径与 .exe/.cmd 等后缀）。"""
    name = re.split(r"[\\/]", token.strip())[-1]
    name = name.strip().lower()
    for suffix in (".exe", ".cmd", ".bat", ".ps1", ".sh", ".com"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name


def _option(rest: str, name: str) -> str:
    """取出 ``-Name value`` 形式参数的值。"""
    match = re.search(
        rf"(?i)(?:^|\s)-{re.escape(name)}\s+(\"[^\"]*\"|'[^']*'|\S+)",
        rest,
    )
    return _unquote(match.group(1)) if match else ""


def _positionals(rest: str) -> list[str]:
    """取出位置参数；跳过 ``-Flag`` 与其取值。"""
    values: list[str] = []
    tokens = re.findall(r"\"[^\"]*\"|'[^']*'|\S+", rest)
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token.startswith("-") and not token.isdigit():
            # 像 -Path 后面通常跟一个值；短开关（-Force 等）不吞下一个词。
            if token.lower().lstrip("-") in {
                "path", "pattern", "filter", "include", "exclude", "literalpath",
                "destination", "source", "value", "name", "encoding", "cwd",
            }:
                skip_next = True
            continue
        values.append(_unquote(token))
    return values


def _pattern_label(pattern: str) -> str:
    """把正则 pattern 还原成可读关键字：``href="([^"]+)"`` → ``href``。"""
    text = str(pattern or "")
    # 先去掉字符类与分组这类纯正则结构，剩下的才是人想看的关键字。
    text = re.sub(r"\[[^\]]*\]", " ", text)
    for _ in range(3):
        text = re.sub(r"\([^()]*\)", " ", text)
    text = text.replace('"', "").replace("'", "")
    text = _REGEX_NOISE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" =<>:,;")
    return _clip(text, 24)


def _is_separator(text: str) -> bool:
    return not text.strip() or bool(_SEPARATOR_TEXT.match(text.strip()))


def _expression_phrase(text: str) -> str:
    """从赋值右侧 / 嵌套表达式里提炼一句说明。"""
    lowered = text.lower()
    if re.search(r"(?i)\[regex\]::matches\b|\.matches\s*\(", text):
        return "统计匹配次数" if ".count" in lowered else "查找匹配内容"
    for pattern, phrase in _EXPRESSION_PHRASES:
        if pattern.search(text):
            return phrase
    return ""


def _join_phrases(phrases: list[str], separator: str = "，然后") -> str:
    """拼接说明片段，顺手去掉连续重复（嵌套循环里很常见）。"""
    merged: list[str] = []
    for phrase in phrases:
        phrase = phrase.strip()
        if phrase and (not merged or merged[-1] != phrase):
            merged.append(phrase)
    return separator.join(merged)


def _looks_like_value(text: str) -> bool:
    """赋值右侧是不是纯值（成员访问、字面量、简单拼接）。"""
    if _expression_phrase(text):
        return False
    if re.search(r"(?i)\b(test-path|join-path|get-|select-|invoke-|remove-|copy-|move-|new-)\b", text):
        return False
    return "|" not in text and "(" not in text.split(".", 1)[0]


def _statement_phrase(statement: str) -> str:
    text = statement.strip()
    if not text:
        return ""

    assignment = _ASSIGNMENT.match(text)
    if assignment:
        rhs = assignment.group("rhs").strip()
        if _looks_like_value(rhs):
            return ""
        return _expression_phrase(rhs) or _statement_phrase(rhs)

    stages = [stage for stage in _split_top_level(text, ("|",)) if stage.strip()]
    phrases: list[str] = []
    for stage in stages:
        phrase = _stage_phrase(stage)
        if phrase:
            phrases.append(phrase)
    return _join_phrases(phrases)


def _split_block(text: str) -> tuple[str, str | None]:
    """把 ``ForEach-Object { ... }`` 拆成 ``("ForEach-Object ", "...")``。"""
    depth = 0
    quote = ""
    start = -1
    for index, char in enumerate(text):
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in _QUOTES:
            quote = char
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
            if depth == 0 and start >= 0:
                return text[:start].strip(), text[start + 1:index]
    return text, None


_COMPARISONS = {
    "-eq": "=",
    "-ne": "≠",
    "-gt": ">",
    "-ge": "≥",
    "-lt": "<",
    "-le": "≤",
    "-like": "匹配",
    "-match": "匹配",
    "-contains": "包含",
    "-in": "属于",
    "==": "=",
    "!=": "≠",
    ">=": "≥",
    "<=": "≤",
    ">": ">",
    "<": "<",
}

_CONDITION = re.compile(
    r"\$_\.[\w.]+\s*(?P<op>-(?:eq|ne|gt|ge|lt|le|like|match|contains|in)|==|!=|>=|<=|>|<)\s*(?P<value>.+)$",
    re.I | re.S,
)


def _condition_phrase(block: str) -> str:
    """把 ``$_.Length -gt 1000`` 这类筛选条件翻成「长度 > 1000」。"""
    match = _CONDITION.search(block.strip())
    if not match:
        return ""
    prop = re.search(r"\$_\.([\w.]+)", block)
    if not prop:
        return ""
    value = _clip(_unquote(match.group("value").strip()), 24)
    return f" {prop.group(1)} {_COMPARISONS.get(match.group('op').lower(), match.group('op'))} {value} "


_FOREACH = re.compile(
    r"(?is)\bforeach\s*\(\s*\$(?P<var>\w+)\s+in\s+(?P<source>.+?)\)\s*$"
)
_QUOTED_ITEMS = re.compile(r"'([^']*)'|\"([^\"]*)\"")


def _foreach_source(head_text: str) -> str:
    """``foreach ($f in Get-ChildItem *.html)`` → 「逐个处理 *.html 文件」。"""
    match = _FOREACH.search(head_text.strip())
    if not match:
        return ""
    source = match.group("source").strip()
    var = match.group("var")
    listing = re.match(r"(?is)^(?:get-childitem|gci|dir|ls)\b(?P<rest>.*)$", source)
    if listing:
        rest = listing.group("rest")
        pattern = _option(rest, "filter") or _option(rest, "include")
        if not pattern:
            values = _positionals(rest)
            pattern = values[0] if values else ""
        if pattern:
            return f"逐个处理 {pattern} 文件"
        return "逐个处理当前目录的文件"
    items = [a or b for a, b in _QUOTED_ITEMS.findall(source)]
    if items:
        return f"逐个处理 {'、'.join(items[:6])}"
    if source.startswith("$"):
        return f"逐个处理 {var} 中的每一项"
    return f"逐个处理 {_clip(source, 24)}"


def _block_phrase(head_text: str, block: str) -> str:
    """脚本块：循环/筛选沿用内部的说明，其余按「命令 + 内部动作」拼。"""
    head, _rest = _first_token(head_text)
    key = _program_key(head)
    inner = command_steps(block)
    if key in {"foreach-object", "foreach", "%", "for", "while", "do"}:
        source = _foreach_source(head_text) if key in {"foreach", "for"} else ""
        if source:
            return _join_phrases([source, *inner])
        return _join_phrases(inner)
    if key in {"where-object", "?", "if"}:
        if key == "if":
            return "条件成立时" + _join_phrases(inner) if inner else "按条件决定是否继续"
        condition = _condition_phrase(block)
        if condition:
            return f"筛选{condition}的项"
        return "筛选：" + _join_phrases(inner) if inner else "筛选符合条件的项"
    if head_text.strip().startswith("["):
        return _join_phrases(inner)
    base = _stage_phrase(head_text) if head_text.strip() else ""
    if base and inner:
        return _join_phrases([base, *inner])
    return base or _join_phrases(inner)


def _stage_phrase(stage: str) -> str:
    text = stage.strip()
    while text.startswith("(") and text.endswith(")") and _balanced(text[1:-1]):
        text = text[1:-1].strip()
    if not text:
        return ""

    head_text, block = _split_block(text)
    if block is not None:
        return _block_phrase(head_text, block)

    # 纯字符串表达式（常见于打印差异/结果）：不必强行猜出具体内容。
    if text[:1] in _QUOTES:
        return "输出一行结果" if "$" in text else ""

    head, rest = _first_token(text)
    if not head:
        return _expression_phrase(text)
    # 纯取值段（``$_.Matches``、``"文本"``、``123``）不承担动作，跳过。
    if head.startswith(("$", "[", "@")) or head[0] in _QUOTES or head[0].isdigit():
        return _expression_phrase(text)
    return _simple_phrase(_program_key(head), rest, head)


def _simple_phrase(key: str, rest: str, raw_head: str = "") -> str:
    """单个管道段的说明。"""
    if not key:
        return ""

    # ---- 列目录 / 查找文件 ----
    if key in {"get-childitem", "gci", "ls", "dir", "find"}:
        path = _option(rest, "path") or _option(rest, "literalpath")
        pattern = _option(rest, "filter") or _option(rest, "include")
        if not path and not pattern:
            values = _positionals(rest)
            first = values[0] if values else ""
            if first and any(char in first for char in "*?["):
                pattern = first
            else:
                path = first
        if not path and not pattern:
            return "列出当前目录内容"
        if pattern and path:
            return f"列出 {path} 下匹配 {pattern} 的文件"
        if pattern:
            return f"查找并列出 {pattern} 文件"
        if any(char in path for char in "*?["):
            return f"查找并列出 {path} 文件"
        return f"列出 {path} 目录内容"

    # ---- 内容搜索 ----
    if key in {"select-string", "sls", "findstr", "grep", "rg"}:
        path = _option(rest, "path") or _option(rest, "literalpath")
        pattern = _option(rest, "pattern")
        if not pattern:
            values = _positionals(rest)
            pattern = values[0] if values else ""
            path = path or (values[1] if len(values) > 1 else "")
        label = _pattern_label(pattern)
        where = f"在 {path} 中" if path else "在文件中"
        return f"{where}搜索包含 {label} 的内容" if label else f"{where}搜索指定内容"

    # ---- 选取 / 排序 / 格式化 ----
    if key in {"select-object", "select"}:
        expanded = _option(rest, "expandproperty")
        if expanded:
            return f"只取 {expanded} 这一项"
        first = _option(rest, "first")
        last = _option(rest, "last")
        if first:
            return f"只取前 {first} 项"
        if last:
            return f"只取最后 {last} 项"
        props = [item.strip() for item in _positionals(rest)]
        props = [item for item in " ".join(props).replace("，", ",").split(",") if item.strip()]
        if props:
            return f"只保留 {'、'.join(props[:6])} 字段"
        return "挑选要显示的字段"
    if key in {"sort-object", "sort"}:
        return "对结果排序"
    if key in {"format-table", "ft", "format-list", "fl"}:
        return "以表格显示" if key in {"format-table", "ft"} else "以列表显示"
    if key in {"measure-object", "measure"}:
        return "统计结果数量"
    if key in {"where-object", "?"}:
        return "筛选符合条件的项"
    if key in {"foreach-object", "%"}:
        return "逐项处理"
    if key in {"unique", "get-unique"}:
        return "去除重复项"

    # ---- 输出 ----
    if key in {"write-output", "write-host", "echo", "print"}:
        text = _unquote(rest)
        if _is_separator(text):
            return ""
        return f"输出「{_clip(text, 32)}」" if len(text) <= 32 else "输出一段文本"
    if key in {"out-string", "out-host", "tee-object", "out-null"}:
        return ""

    # ---- 内容读写 ----
    if key in {"get-content", "gc", "cat", "type", "head", "tail"}:
        target = _option(rest, "path") or (_positionals(rest) or [""])[0]
        return f"读取 {target} 的内容" if target else "读取文件内容"
    if key in {"set-content", "sc", "out-file", ">"}:
        target = _option(rest, "path") or _option(rest, "filepath")
        return f"写入 {target}" if target else "写入文件"
    if key in {"add-content", "ac"}:
        target = _option(rest, "path")
        return f"向 {target} 追加内容" if target else "追加文件内容"
    if key in {"new-item", "ni", "mkdir", "md", "touch"}:
        target = _option(rest, "path") or (_positionals(rest) or [""])[0]
        return f"新建 {target}" if target else "新建文件或目录"

    # ---- 删除 / 复制 / 移动 ----
    if key in {"remove-item", "ri", "rm", "del", "erase", "rd", "rmdir"}:
        target = _option(rest, "path") or (_positionals(rest) or [""])[0]
        recursive = bool(re.search(r"(?i)(-recurse|-r\b|/s\b|/f\b)", rest))
        prefix = "递归删除" if recursive else "删除"
        return f"{prefix} {target}" if target else f"{prefix}文件"
    if key in {"copy-item", "cp", "copy", "robocopy", "xcopy"}:
        values = _positionals(rest)
        if len(values) >= 2:
            return f"把 {values[0]} 复制到 {values[1]}"
        return "复制文件"
    if key in {"move-item", "mv", "move", "ren", "rename-item"}:
        values = _positionals(rest)
        if len(values) >= 2:
            return f"把 {values[0]} 移动到 {values[1]}"
        return "移动或重命名文件"

    # ---- 路径 / 环境 ----
    if key in {"test-path"}:
        return "检查路径是否存在"
    if key in {"join-path", "resolve-path", "convert-path"}:
        return "拼接或解析路径"
    if key in {"get-location", "pwd"}:
        return "显示当前目录"
    if key in {"set-location", "cd", "push-location", "pop-location"}:
        target = (_positionals(rest) or [""])[0]
        return f"切换到 {target} 目录" if target else "切换工作目录"
    if key in {"get-item", "gi"}:
        target = (_positionals(rest) or [""])[0]
        return f"查看 {target} 的信息" if target else "查看文件信息"
    if key in {"get-process", "tasklist", "ps"}:
        return "查看进程列表"
    if key in {"stop-process", "taskkill", "kill", "killall", "pkill"}:
        target = (_positionals(rest) or [""])[0]
        return f"结束进程 {target}" if target else "结束进程"
    if key in {"start-process", "start", "saps"}:
        target = (_positionals(rest) or [""])[0]
        return f"启动程序 {target}" if target else "启动程序"

    # ---- 网络 ----
    if key in {"invoke-webrequest", "iwr", "wget"}:
        return "从网络下载内容"
    if key in {"invoke-restmethod", "irm"}:
        return "请求网络接口"
    if key == "curl":
        return "请求网络地址"

    # ---- Git ----
    if key == "git":
        sub = (_positionals(rest) or [""])[0].lower()
        return _GIT_VERBS.get(sub, f"执行 Git 操作 {sub}".strip())

    # ---- 包管理器 / 构建 / 运行 ----
    if key in {"npm", "pnpm", "yarn"}:
        values = _positionals(rest)
        sub = values[0].lower() if values else ""
        if sub == "run" and len(values) > 1:
            return f"运行 {key} 脚本 {values[1]}"
        return _PACKAGE_SCRIPTS.get(sub, f"运行 {key} 命令")
    if key in {"python", "python3", "py"}:
        return _python_phrase(rest)
    if key in {"node", "deno", "bun"}:
        script = next((item for item in _positionals(rest) if item.endswith((".js", ".ts", ".mjs"))), "")
        return f"运行 Node 脚本 {script}" if script else "运行 Node 命令"
    if key == "pytest":
        return "运行测试"
    if key in {"pip", "uv"}:
        return "管理 Python 依赖"
    if key in {"dotnet", "msbuild"}:
        return "构建或运行 .NET 项目"
    if key in {"cargo", "go", "make", "cmake", "gradle", "mvn"}:
        return f"运行 {key} 构建命令"
    if key in {"docker", "docker-compose"}:
        return "执行 Docker 操作"

    return f"运行 {raw_head or key} 命令"


def _python_phrase(rest: str) -> str:
    values = _positionals(rest)
    joined = " ".join(values).lower()
    if "pytest" in joined or "unittest" in joined or " pytest" in f" {joined}":
        return "运行 Python 测试"
    script = next((item for item in values if item.endswith(".py")), "")
    if script:
        return f"运行 Python 脚本 {script}"
    if "-m http.server" in joined or "http.server" in joined:
        return "启动本地 HTTP 服务"
    if "-m pip" in joined:
        return "管理 Python 依赖"
    return "执行 Python 命令"


_OUTPUT_KEYS = {"write-output", "write-host", "echo", "print"}
#: ``--- href check ---`` 这类用作分段标题的输出。
_SECTION = re.compile(r"^[-=*#_~]{2,}\s*(?P<core>.+?)\s*[-=*#_~]{2,}$")


def _output_text(statement: str) -> str:
    """语句是不是「输出一段文字」；是则返回去掉引号的文字。"""
    head, rest = _first_token(statement)
    if _program_key(head) not in _OUTPUT_KEYS:
        return ""
    return _unquote(rest).strip()


def _section_label(text: str) -> str:
    """把 ``--- href check ---`` 这类标题翻成能看懂的小标题。"""
    match = _SECTION.match(text.strip())
    if not match:
        return ""
    core = match.group("core").strip()
    low = core.lower()
    if not core or "done" in low or core in {"完成", "结束"}:
        return ""
    if "href" in low or "link" in low or "链接" in core:
        return "检查页面链接"
    if "tag" in low or "标签" in core:
        return "检查标签配对"
    if "size" in low or "length" in low or "大小" in core:
        return "汇总文件大小"
    return f"「{_clip(core, 20)}」"


def command_steps(command: str) -> list[str]:
    """把命令拆成若干条可读的操作说明（最多 :data:`MAX_STEPS` 条）。"""
    text = str(command or "").strip()
    if not text:
        return []
    steps: list[str] = []
    outputs: list[str] = []
    pending = ""
    for statement in _split_top_level(text, ("\n", ";", "&&", "||")):
        raw = statement.strip()
        if not raw:
            continue
        # 命令里的分段标题（--- xxx ---）当成下一条说明的小标题。
        label = _section_label(_output_text(raw))
        if label:
            pending = label
            continue

        phrase = _statement_phrase(raw)
        if not phrase:
            continue
        if pending:
            phrase = f"{pending}：{phrase}"
            pending = ""
        if phrase.startswith("输出"):
            # 多步命令里的 echo 多为分隔/进度提示，不占审批卡片的位置。
            outputs.append(phrase)
            continue
        if steps and steps[-1] == phrase:
            continue
        steps.append(_clip(phrase, 90))
        if len(steps) >= MAX_STEPS:
            break
    if not steps:
        steps = outputs[:MAX_STEPS]
    return steps


def describe_command(command: str) -> str:
    """返回一句人话说明；完全无法识别时返回空串，由调用方回落到原文。"""
    steps = command_steps(command)
    if not steps:
        return ""
    return _clip("；".join(steps), MAX_SUMMARY)
