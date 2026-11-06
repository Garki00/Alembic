# tools/errors.py
"""工具的统一输出约定：失败一律返回 ``Error:`` 开头，不抛异常。

为什么必须统一：模型是靠字符串前缀判断「这次工具调用失败了」的。
此前天气返回 ``ERROR:``、时间返回 ``Error:``，大小写不一致会让模型漏判
失败，然后在错误的前提上继续推理；而抛异常则会让整轮对话中断。

失败格式：:

    Error: [<code>] <一句话说明>
    <可选：下一步该怎么做>

- ``code`` 用下划线小写、保持稳定，便于提示词与测试引用；
- 建议放在第二行，避免挤在一起；
- 成功时的输出不受此约定限制。
"""

from __future__ import annotations

ERROR_PREFIX = "Error:"


def tool_error(code: str, message: str, *, hint: str = "") -> str:
    """拼一条失败结果：``Error: [code] message``，可选追加建议行。"""
    head = f"{ERROR_PREFIX} [{code}] {message}" if code else f"{ERROR_PREFIX} {message}"
    return f"{head}\n{hint}" if hint else head


def is_error(text: str) -> bool:
    """判断一段工具输出是否表示失败。"""
    return isinstance(text, str) and text.startswith(ERROR_PREFIX)


def truncate(text: str, max_chars: int, *, keep_tail: bool = False) -> str:
    """按字符数截断工具输出，并留下明确的截断标记。

    Args:
        text: 原始输出。
        max_chars: 允许的最大字符数（含标记）。
        keep_tail: True 时保留尾部（命令输出里的报错通常在最后），
            False 时只保留开头（读文件、列表更适合这样）。
    """
    if max_chars <= 0 or len(text) <= max_chars:
        return text

    head_budget = max_chars if not keep_tail else int(max_chars * 0.7)
    tail_budget = max_chars - head_budget if keep_tail else 0
    omitted = len(text) - head_budget - tail_budget
    marker = f"\n…[已截断：共 {len(text)} 字符，省略 {omitted} 字符]…"

    if not keep_tail:
        return text[:head_budget] + marker
    head = text[:head_budget]
    tail = text[-tail_budget:] if tail_budget > 0 else ""
    return f"{head}{marker}\n{tail}"
