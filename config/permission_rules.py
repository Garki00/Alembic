# config/permission_rules.py
"""Permission rule sets: allow / ask / deny rules evaluated before mode defaults.

A rule matches a tool call by tool name and an optional subject: the command, file
path, or URL the call targets. A rule without a subject matches every call of that
tool. Subjects support an exact match, a ``prefix:*`` prefix match, and ``*``
wildcards, so ``run_command`` + ``pytest:*`` allows only commands starting with
``pytest``.

Rules live in two places:

- user rules in ``<data>/permissions.json`` may allow, ask, or deny;
- project rules in ``<workspace>/.alembic/permissions.json`` may only ask or deny,
  because a repository must not be able to grant itself permissions.

The permission engine evaluates deny rules first, then ask rules, then allow rules;
mode defaults apply only when no rule matched.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

BEHAVIORS = ("allow", "ask", "deny")
#: Project files may only restrict; an allow rule there is dropped on load.
PROJECT_BEHAVIORS = ("deny", "ask")
MAX_RULES = 200
MAX_RULE_CONTENT = 500
MAX_TOOL_NAME = 80
FILE_VERSION = 1
PROJECT_RULES_RELPATH = Path(".alembic") / "permissions.json"
#: Tools that share one rule identity: allowing an edit of ``a.py`` through
#: ``write_file`` also covers ``edit_file`` on the same path.
TOOL_ALIASES = {
    "write_file": frozenset({"write_file", "edit_file"}),
    "edit_file": frozenset({"write_file", "edit_file"}),
}
#: Argument keys used as the rule subject, in priority order.
SUBJECT_KEYS = ("command", "path", "file_path", "url", "pattern", "source", "destination")
WEB_TOOLS = frozenset({"fetch_url", "web_search"})


@dataclass(frozen=True)
class PermissionRule:
    """One rule: a tool name plus an optional subject pattern."""

    tool: str
    content: str = ""

    def as_dict(self) -> dict[str, str]:
        item = {"tool": self.tool}
        if self.content:
            item["content"] = self.content
        return item

    def describes(self, tool_name: str) -> bool:
        if self.tool == "*":
            return True
        allowed = TOOL_ALIASES.get(tool_name, frozenset({tool_name}))
        return self.tool in allowed


@dataclass
class PermissionRuleset:
    """Three ordered rule lists; an empty list means the behavior has no rules."""

    allow: list[PermissionRule] = field(default_factory=list)
    ask: list[PermissionRule] = field(default_factory=list)
    deny: list[PermissionRule] = field(default_factory=list)

    def rules(self, behavior: str) -> list[PermissionRule]:
        return {"allow": self.allow, "ask": self.ask, "deny": self.deny}[behavior]

    def count(self) -> int:
        return len(self.allow) + len(self.ask) + len(self.deny)

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": FILE_VERSION,
            "rules": {behavior: [rule.as_dict() for rule in self.rules(behavior)] for behavior in BEHAVIORS},
        }

    def merged(self, other: "PermissionRuleset | None") -> "PermissionRuleset":
        """Union of both rule sets, used for user + project rules on read."""
        if other is None:
            return self
        return PermissionRuleset(
            allow=[*self.allow, *other.allow],
            ask=[*self.ask, *other.ask],
            deny=[*self.deny, *other.deny],
        )


def _clean_tool(value: Any) -> str:
    tool = str(value or "").strip()
    if not tool:
        raise ValueError("规则缺少工具名")
    if len(tool) > MAX_TOOL_NAME:
        raise ValueError(f"工具名过长：{tool[:32]}…")
    if tool != "*" and not re.fullmatch(r"[A-Za-z0-9_.:-]+", tool):
        raise ValueError(f"工具名不合法：{tool}")
    return tool


def _clean_content(value: Any) -> str:
    content = str(value or "").strip()
    if len(content) > MAX_RULE_CONTENT:
        raise ValueError("规则内容过长")
    return content


def normalize_ruleset(data: Mapping[str, Any] | None, *, allow_allow: bool = True) -> PermissionRuleset:
    """Validate a serialized rule set; raise ``ValueError`` with a user-facing message."""
    result = PermissionRuleset()
    if not data:
        return result
    raw_rules = data.get("rules", data)
    if not isinstance(raw_rules, Mapping):
        raise ValueError("rules 需要是对象")
    for behavior in BEHAVIORS:
        if behavior == "allow" and not allow_allow:
            continue
        items = raw_rules.get(behavior, [])
        if not isinstance(items, list):
            raise ValueError(f"{behavior} 需要是数组")
        for item in items:
            if not isinstance(item, Mapping):
                raise ValueError(f"{behavior} 规则需要是对象")
            rule = PermissionRule(tool=_clean_tool(item.get("tool")), content=_clean_content(item.get("content")))
            target = result.rules(behavior)
            if len(target) >= MAX_RULES:
                raise ValueError(f"{behavior} 规则最多 {MAX_RULES} 条")
            if not any(existing.tool == rule.tool and existing.content == rule.content for existing in target):
                target.append(rule)
    return result


def _wildcard_matches(pattern: str, subject: str) -> bool:
    escaped = re.escape(pattern).replace(r"\*", ".*")
    return re.fullmatch(escaped, subject) is not None


def matches_content(pattern: str, subject: str) -> bool:
    """Match one rule subject: exact, ``prefix:*``, or ``*`` wildcards."""
    if not pattern:
        return True
    if pattern.endswith(":*"):
        prefix = pattern[:-2]
        return subject == prefix or subject.startswith(f"{prefix} ") or subject.startswith(f"{prefix}\t")
    if "*" in pattern:
        return _wildcard_matches(pattern, subject)
    return subject == pattern


def rule_subject(tool_name: str, args: Mapping[str, Any] | None) -> str:
    """Extract the subject a rule is compared against; empty string when the call has none."""
    if not isinstance(args, Mapping):
        return ""
    if tool_name in WEB_TOOLS:
        url = args.get("url")
        if isinstance(url, str) and url.strip():
            try:
                host = urlparse(url.strip()).hostname or ""
            except ValueError:
                host = ""
            if host:
                return f"domain:{host.lower().rstrip('.')}"
    for key in SUBJECT_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def rule_matches(rule: PermissionRule, tool_name: str, args: Mapping[str, Any] | None) -> bool:
    if not rule.describes(tool_name):
        return False
    if not rule.content:
        return True
    subject = rule_subject(tool_name, args)
    return bool(subject) and matches_content(rule.content, subject)


def first_match(rules: Iterable[PermissionRule], tool_name: str, args: Mapping[str, Any] | None) -> PermissionRule | None:
    for rule in rules:
        if rule_matches(rule, tool_name, args):
            return rule
    return None


def load_ruleset(path: str | Path, *, allow_allow: bool = True) -> PermissionRuleset:
    """Read a rule file; a missing file is an empty set and an invalid file is reported and ignored."""
    file = Path(path)
    if not file.exists():
        return PermissionRuleset()
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
        return normalize_ruleset(data, allow_allow=allow_allow)
    except (OSError, ValueError) as exc:
        logger.warning("读取权限规则失败，已忽略 %s：%s", file, exc)
        return PermissionRuleset()


def load_project_rules(workspace: str | Path | None) -> PermissionRuleset:
    """Read workspace-local rules; only the restricting behaviors are honored."""
    if not workspace:
        return PermissionRuleset()
    return load_ruleset(Path(workspace) / PROJECT_RULES_RELPATH, allow_allow=False)


def save_ruleset(path: str | Path, ruleset: PermissionRuleset) -> None:
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_name(file.name + ".tmp")
    tmp.write_text(json.dumps(ruleset.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(file)


class PermissionRulesStore:
    """Persistence for user permission rules in the application data directory."""

    def __init__(self, path: str | Path) -> None:
        self.file = Path(path)

    def get(self) -> PermissionRuleset:
        return load_ruleset(self.file)

    def update(self, data: Mapping[str, Any]) -> PermissionRuleset:
        ruleset = normalize_ruleset(data)
        save_ruleset(self.file, ruleset)
        return ruleset
