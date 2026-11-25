# tools/bash_analysis.py
"""Conservative Bash command analysis for read-only classification.

The analyzer only needs to answer one question safely: can the whole command be
proven to consist of simple, non-interactive invocations with no dynamic words and
no output redirection? Anything the scanner does not fully understand is reported
as unsupported, so the permission layer keeps asking for approval.

Supported syntax: simple commands, environment assignments, pipelines (``|``,
``|&``), ``&&`` / ``||`` chains, ``;`` and newline sequences, and input/output
redirects. Command substitution, parameter expansion, backticks, subshells,
brace groups, control structures, background jobs, heredocs, and process
substitution are all rejected.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MAX_COMMAND_LENGTH = 10_000

UNSUPPORTED_KEYWORDS = frozenset(
    {
        "if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done",
        "case", "esac", "function", "select", "coproc", "!",
    }
)

#: Characters that always mean the command is not statically analyzable.
DYNAMIC_CHARACTERS = ("$", "`")

ASSIGNMENT_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


@dataclass(frozen=True)
class Redirect:
    operator: str
    target: str


@dataclass
class Invocation:
    """One simple command extracted from the script."""

    argv: list[str]
    text: str
    redirects: list[Redirect] = field(default_factory=list)
    env_assignments: list[tuple[str, str]] = field(default_factory=list)
    operator_before: str = ""

    @property
    def name(self) -> str:
        return self.argv[0] if self.argv else ""


@dataclass
class Analysis:
    commands: list[Invocation] = field(default_factory=list)
    has_parse_error: bool = False
    has_dynamic_words: bool = False
    has_unsupported_syntax: bool = False
    has_redirects: bool = False

    @property
    def permission_safe(self) -> bool:
        return not (self.has_parse_error or self.has_dynamic_words or self.has_unsupported_syntax)


class _Scanner:
    """Split a command line into words, redirects, and operators."""

    def __init__(self, command: str) -> None:
        self.command = command
        self.length = len(command)
        self.index = 0
        self.dynamic = False
        self.unsupported = False
        self.parse_error = False

    def _peek(self, offset: int = 0) -> str:
        position = self.index + offset
        return self.command[position] if position < self.length else ""

    def scan(self) -> list[Invocation]:
        invocations: list[Invocation] = []
        words: list[str] = []
        assignments: list[tuple[str, str]] = []
        redirects: list[Redirect] = []
        command_start: int | None = None
        pending_operator = ""
        command_text = ""

        def flush(operator_after: str) -> None:
            nonlocal words, assignments, redirects, command_start, command_text
            if words or redirects:
                argv = list(words)
                invocations.append(
                    Invocation(
                        argv=argv,
                        text=command_text.strip(),
                        redirects=list(redirects),
                        env_assignments=list(assignments),
                        operator_before=pending_operator,
                    )
                )
            words = []
            assignments = []
            redirects = []
            command_start = None
            command_text = ""

        while self.index < self.length:
            character = self.command[self.index]
            if character in " \t\r":
                self.index += 1
                continue
            if character == "\n":
                flush("sequence")
                pending_operator = "sequence"
                self.index += 1
                continue
            if character == ";":
                if self._peek(1) == ";":
                    self.unsupported = True
                    self.index += 2
                    continue
                flush("sequence")
                pending_operator = "sequence"
                self.index += 1
                continue
            if character == "&":
                if self._peek(1) == "&":
                    flush("&&")
                    pending_operator = "&&"
                    self.index += 2
                    continue
                # A bare ampersand is a background job, which is not analyzable here.
                self.unsupported = True
                self.index += 1
                continue
            if character == "|":
                if self._peek(1) == "&":
                    flush("|&")
                    pending_operator = "|&"
                    self.index += 2
                else:
                    flush("|")
                    pending_operator = "|"
                    self.index += 1
                continue
            if character in "()":
                self.unsupported = True
                self.index += 1
                continue

            start = self.index
            redirect = self._read_redirect()
            if redirect is not None:
                redirects.append(redirect)
                continue

            word = self._read_word()
            if word is None:
                self.parse_error = True
                break
            if command_start is None:
                command_start = start
            if not words and self._is_assignment(word):
                name, value = word.split("=", 1)
                assignments.append((name, value))
                continue
            words.append(word)
            if self.index >= self.length or self.command[self.index] in " \t\r\n;|&()":
                command_text = self.command[command_start : self.index]
            self.index = self.index

        flush("")
        return invocations

    def _read_redirect(self) -> Redirect | None:
        start = self.index
        index = self.index
        while index < self.length and self.command[index].isdigit():
            index += 1
        operator = ""
        for candidate in (">>", ">|", ">&", "<<<", "<<", "<&", "<>", ">", "<"):
            if self.command.startswith(candidate, index):
                operator = candidate
                break
        if not operator:
            return None
        self.index = index + len(operator)
        while self.index < self.length and self.command[self.index] in " \t":
            self.index += 1
        target = self._read_word()
        if target is None:
            self.parse_error = True
            return Redirect(operator, "")
        return Redirect(operator, target)

    def _read_word(self) -> str | None:
        parts: list[str] = []
        quote = ""
        self.index_start = self.index
        while self.index < self.length:
            character = self.command[self.index]
            if quote == "'":
                if character == "'":
                    quote = ""
                else:
                    parts.append(character)
                self.index += 1
                continue
            if quote == '"':
                if character == '"':
                    quote = ""
                    self.index += 1
                    continue
                if character == "\\" and self._peek(1) in {'"', "\\", "$", "`", "\n"}:
                    parts.append(self._peek(1))
                    self.index += 2
                    continue
                if character in DYNAMIC_CHARACTERS:
                    self.dynamic = True
                parts.append(character)
                self.index += 1
                continue
            if character in " \t\r\n":
                break
            if character in ";|&()":
                break
            if character in "'\"":
                quote = character
                self.index += 1
                continue
            if character == "\\":
                escaped = self._peek(1)
                if not escaped:
                    self.parse_error = True
                    return None
                parts.append(escaped)
                self.index += 2
                continue
            if character in DYNAMIC_CHARACTERS:
                self.dynamic = True
            if character in "{}" and self._peek(1) in ",.":
                self.dynamic = True
            if character in "<>":
                # A redirect operator inside a word is not something we model.
                self.unsupported = True
            parts.append(character)
            self.index += 1
        if quote:
            self.parse_error = True
            return None
        value = "".join(parts)
        return value if parts else None

    def _is_assignment(self, word: str) -> bool:
        name, separator, _value = word.partition("=")
        if not separator or not name:
            return False
        if not (name[0].isalpha() or name[0] == "_"):
            return False
        return all(character in ASSIGNMENT_CHARS for character in name)


def analyze(command: str) -> Analysis:
    """Parse ``command`` into simple invocations, rejecting anything non-static."""
    text = (command or "").strip()
    if not text:
        return Analysis()
    if len(command) > MAX_COMMAND_LENGTH:
        return Analysis(has_parse_error=True)

    scanner = _Scanner(command)
    try:
        commands = scanner.scan()
    except (IndexError, ValueError):
        return Analysis(has_parse_error=True)

    analysis = Analysis(
        commands=commands,
        has_parse_error=scanner.parse_error,
        has_dynamic_words=scanner.dynamic,
        has_unsupported_syntax=scanner.unsupported,
        has_redirects=any(item.redirects for item in commands),
    )
    for item in commands:
        if item.argv and item.argv[0] in UNSUPPORTED_KEYWORDS:
            analysis.has_unsupported_syntax = True
    return analysis
