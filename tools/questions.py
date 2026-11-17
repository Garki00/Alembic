# tools/questions.py
"""Question tool and terminal question channel.

Use a question when information is needed to continue; the answer is free text or
one of the offered options, unlike an approval whose answer is once / always / deny.
The tool waits synchronously on the tool thread; the web UI delivers events and
answers through the ``web.questions`` relay, while the CLI reads terminal input.
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
    """The user interrupted the answer (terminal EOF / Ctrl-C, or the web stream closed)."""


@dataclass(frozen=True)
class Question:
    """One pending question, directly serializable to an SSE event."""

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


#: Question channel: returns the user's answer; ``None`` means no answer (timeout / stream closed).
Asker = Callable[[Question], str | None]


def console_asker(question: Question) -> str | None:
    """Ask on the command line: print the question and options, then read one input line."""
    print("\n[需要你的输入]")
    print(question.question)
    for index, option in enumerate(question.options, 1):
        print(f"  {index}. {option}")
    prompt = "选择序号或直接输入回答："
    try:
        raw = input(prompt).strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise QuestionCancelled("The user interrupted the answer.") from exc

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
    """Build the question tool. Without a question channel it returns an error instead of silently skipping."""

    @tool
    def ask_user_question(
        question: str,
        options: list[str] = [],
        allow_free_text: bool = True,
    ) -> str:
        """Ask the user a question and wait for the answer; use it for information that cannot be confirmed from the workspace.

        Use it only when a user decision is genuinely required: the goal is
        ambiguous, there is a choice between approaches, or a parameter is missing
        that only the user knows. Do not use it to confirm facts you can look up.

        Args:
            question: The question to ask the user; keep it to one clear sentence.
            options: Optional choices (at most 6) the user can click, and free text is allowed as well.
            allow_free_text: Whether the user may type text outside the options.

        Returns:
            The user's answer as text; returns ``Error:`` on timeout, cancellation,
            or when no question channel is available.
        """
        text = (question or "").strip()
        if not text:
            return tool_error("empty_question", "question must not be empty.")
        if ctx.asker is None:
            return tool_error(
                "no_ask_channel",
                "No question channel is available, so the tool cannot wait for the user's answer.",
                hint="Continue with the information you have; if something truly cannot be decided, state in your reply what the user needs to provide.",
            )

        cleaned = _clean_options(options)
        if len(cleaned) > MAX_OPTIONS:
            return tool_error(
                "too_many_options",
                f"At most {MAX_OPTIONS} options are allowed; {len(cleaned)} were provided.",
                hint="Merge or remove options and ask again; do not assume the user can see truncated options.",
            )
        if not cleaned and not allow_free_text:
            return tool_error(
                "no_options",
                "When allow_free_text=False, at least one option is required.",
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
                "The conversation was interrupted while waiting, and the user did not answer.",
                hint="Do not repeat the same question verbatim; continue with read-only work, or leave the question in your reply.",
            )
        except Exception as exc:  # a channel failure must not break the whole turn
            return tool_error("question_failed", f"Question channel failed: {exc}.")

        if answer is None:
            return tool_error(
                "question_timeout",
                "Timed out waiting for the user to answer; no answer was received.",
                hint="Do not retry verbatim; proceed with the most reasonable assumption and state it in your reply.",
            )
        answer = str(answer).strip()
        return f"User answer: {answer}" if answer else "User answer: (empty)"

    return [ask_user_question]
