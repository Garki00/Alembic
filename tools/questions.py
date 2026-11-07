# tools/questions.py
"""向用户提问的工具与终端提问通道。

与审批的区别：

- 审批是「这个操作能不能执行」，回答只会是 once / always / deny；
- 提问是「我需要一个信息才能继续」，回答是自由文本或选项之一。

工具在工具线程里同步等待，因此网页端用 ``web.questions`` 的中继把 SSE
事件和 HTTP 回执接起来；命令行直接用终端 ``input()``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from uuid import uuid4

from langchain.tools import tool

from tools.context import ToolContext
from tools.errors import tool_error

MAX_OPTIONS = 6


class QuestionCancelled(Exception):
    """用户中断回答（终端 EOF / Ctrl-C，或网页流已断开）。"""


@dataclass(frozen=True)
class Question:
    """一次待回答的提问，可直接序列化成 SSE 事件。"""

    id: str
    question: str
    options: tuple[str, ...] = ()
    allow_free_text: bool = True
    session_id: str = "default"

    def as_event(self) -> dict:
        return {
            "id": self.id,
            "question": self.question,
            "options": list(self.options),
            "allow_free_text": self.allow_free_text,
        }


#: 提问通道：返回用户回答；``None`` 表示没有回答（超时/流断开）。
Asker = Callable[[Question], str | None]


def console_asker(question: Question) -> str | None:
    """命令行提问：打印问题与选项，读一行输入。"""
    print("\n[需要你的输入]")
    print(question.question)
    for index, option in enumerate(question.options, 1):
        print(f"  {index}. {option}")
    prompt = "选择序号或直接输入回答："
    try:
        raw = input(prompt).strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise QuestionCancelled("用户中断了回答。") from exc

    if raw and raw.isdigit() and question.options:
        index = int(raw)
        if 1 <= index <= len(question.options):
            return question.options[index - 1]
    return raw


def _clean_options(options: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in options or ():
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return tuple(cleaned)


def build_question_tools(ctx: ToolContext) -> list:
    """构造提问工具。没有提问通道时返回错误而不是静默跳过。"""

    @tool
    def ask_user_question(
        question: str,
        options: list[str] = [],
        allow_free_text: bool = True,
    ) -> str:
        """向用户提出一个问题并等待回答，适合补齐无法从工作区确认的信息。

        只在确实需要用户决定时使用：例如目标不明确、要在多个方案中选择、
        缺少只有用户知道的参数。不要用它确认自己已经能查到的事实。

        Args:
            question: 要问用户的问题，尽量一句话说清楚。
            options: 可选的候选项（最多 6 个），用户可点选，也可自由输入。
            allow_free_text: 是否允许用户输入选项之外的文字。

        Returns:
            用户回答的文本；超时、被取消或没有提问通道时返回 ``Error:``。
        """
        text = (question or "").strip()
        if not text:
            return tool_error("empty_question", "question 不能为空。")
        if ctx.asker is None:
            return tool_error(
                "no_ask_channel",
                "当前没有可用的提问通道，无法等待用户回答。",
                hint="请先根据现有信息继续；确实无法判断时，在回复里说明需要用户补充什么。",
            )

        cleaned = _clean_options(options)
        if len(cleaned) > MAX_OPTIONS:
            return tool_error(
                "too_many_options",
                f"最多只能提供 {MAX_OPTIONS} 个选项，这次收到 {len(cleaned)} 个。",
                hint="请合并或删减选项后重新提问，不要假设用户能看到被截断的选项。",
            )
        if not cleaned and not allow_free_text:
            return tool_error(
                "no_options",
                "allow_free_text=False 时至少需要提供一个选项。",
            )

        request = Question(
            id=f"q-{uuid4().hex[:10]}",
            question=text,
            options=cleaned,
            allow_free_text=bool(allow_free_text),
            session_id=ctx.session_id,
        )
        try:
            answer = ctx.asker(request)
        except QuestionCancelled:
            return tool_error(
                "question_cancelled",
                "等待回答时对话被中断，用户没有回答。",
                hint="不要原样再问一次；可以先用只读方式继续，或在回复里留下问题。",
            )
        except Exception as exc:  # 通道异常不应中断整轮对话
            return tool_error("question_failed", f"提问通道出错：{exc}。")

        if answer is None:
            return tool_error(
                "question_timeout",
                "等待用户回答超时，没有收到回答。",
                hint="不要原样重试；先按最合理的假设继续，并在回复里说明这个假设。",
            )
        answer = str(answer).strip()
        return f"用户回答：{answer}" if answer else "用户回答：（空）"

    return [ask_user_question]
