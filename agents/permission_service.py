# agents/permission_service.py
"""Permission decisions: rules first, then the mode defaults.

The order mirrors the capability model rather than a per-tool approval table:

1. deny rules (user + project);
2. ask rules (user + project);
3. plan gate: plan only lets read-only / explicitly session-scoped tools through and
   asks for non-destructive extension tools;
4. ``always_ask`` tools ask in every mode;
5. ``yolo`` allows everything that survived the restricting rules;
6. allow rules;
7. ``edit`` mode accepts in-workspace edit tools;
8. build evaluation: read-only tools pass, high/critical risk or side effects ask.

Session grants ("always allow") are applied by the caller only when the decision is
an unforced ask: an explicit ask rule cannot be overridden by a remembered grant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from config.permission_rules import PermissionRuleset, first_match
from tools.readonly_policy import is_read_only_command
from tools.registry import capability_for

ALLOW = "allow"
ASK = "ask"
DENY = "deny"


@dataclass(frozen=True)
class PermissionConfig:
    """Permission policy switches that are not per-tool."""

    #: High-risk tools ask by default; set it only for fully isolated hosts.
    auto_approve_high_risk: bool = False


@dataclass(frozen=True)
class PermissionDecision:
    """Result of evaluating one tool call."""

    behavior: str
    rule_id: str
    reason: str = ""
    capability: dict[str, Any] = field(default_factory=dict)
    #: Tool-error code used when ``behavior`` is ``deny``.
    code: str = ""
    #: False when the ask came from an explicit rule, so "always allow" must not skip it.
    session_grant_allowed: bool = True


def _decision(
    behavior: str,
    rule_id: str,
    capability: Mapping[str, Any],
    reason: str = "",
    *,
    code: str = "",
    session_grant_allowed: bool = True,
) -> PermissionDecision:
    return PermissionDecision(
        behavior=behavior,
        rule_id=rule_id,
        reason=reason,
        capability=dict(capability),
        code=code,
        session_grant_allowed=session_grant_allowed,
    )


def _allow(rule_id: str, capability: Mapping[str, Any], reason: str = "") -> PermissionDecision:
    return _decision(ALLOW, rule_id, capability, reason)


def _ask(
    rule_id: str,
    capability: Mapping[str, Any],
    reason: str = "",
    *,
    session_grant_allowed: bool = True,
) -> PermissionDecision:
    return _decision(ASK, rule_id, capability, reason, session_grant_allowed=session_grant_allowed)


def _deny(
    rule_id: str,
    capability: Mapping[str, Any],
    reason: str,
    *,
    code: str,
) -> PermissionDecision:
    return _decision(DENY, rule_id, capability, reason, code=code)


def _is_true(value: Any) -> bool:
    return value is True


def _plan_gate(mode: str, capability: Mapping[str, Any]) -> PermissionDecision | None:
    if mode != "plan":
        return None
    read_only = _is_true(capability.get("read_only"))
    destructive = _is_true(capability.get("destructive"))
    needs_approval = _is_true(capability.get("needs_approval"))

    if read_only and not destructive and not needs_approval:
        return _allow("mode.plan.read_only", capability, "Plan mode allows read-only tools")

    if read_only and not destructive:
        # Approval-bearing read tools (for example URL fetching) still ask below.
        return None

    if capability.get("permission_name") == "mcp" and not destructive:
        return _ask(
            "mode.plan.mcp",
            capability,
            "Plan mode requires approval for extension tools that are not read-only",
        )

    if (
        _is_true(capability.get("allowed_in_plan_mode"))
        and capability.get("side_effect_scope") == "session"
        and not destructive
        and not needs_approval
    ):
        return _allow(
            "mode.plan.session",
            capability,
            "Plan mode allows this non-destructive session action",
        )

    return _deny(
        "mode.plan.non_read_only",
        capability,
        "Plan mode only allows read-only, non-destructive tools",
        code="plan_mode",
    )


def _build_evaluation(
    mode: str,
    capability: Mapping[str, Any],
    config: PermissionConfig,
) -> PermissionDecision:
    read_only = _is_true(capability.get("read_only"))
    destructive = _is_true(capability.get("destructive"))
    needs_approval = _is_true(capability.get("needs_approval"))
    scope = str(capability.get("side_effect_scope") or "none")
    risk = str(capability.get("risk_level") or "medium")

    if read_only and not destructive and not needs_approval:
        return _allow("mode.read_only", capability, "Read-only tools need no approval")

    if risk == "critical":
        return _ask("mode.critical_risk", capability, "Critical-risk tools require approval")

    if risk == "high" and not config.auto_approve_high_risk:
        return _ask("mode.high_risk", capability, "High-risk tools require approval")

    if scope == "session" and risk == "low" and not destructive and not needs_approval:
        return _allow("mode.session_state", capability, "Low-risk session state updates are allowed")

    if needs_approval or destructive or scope != "none":
        return _ask("mode.side_effect", capability, "Tool has side effects and requires approval")

    return _allow("mode.low_risk", capability, "Low-risk tools need no approval")


def evaluate(
    tool_name: str,
    args: Mapping[str, Any] | None,
    *,
    mode: str,
    rules: PermissionRuleset | None = None,
    project_rules: PermissionRuleset | None = None,
    config: PermissionConfig | None = None,
    shell: str = "",
) -> PermissionDecision:
    """Evaluate one tool call against rules, the plan gate, and the mode defaults."""
    capability = capability_for(tool_name=tool_name)
    command = args.get("command") if isinstance(args, Mapping) else None
    if tool_name == "run_command" and isinstance(command, str) and command.strip():
        if is_read_only_command(command, shell=shell):
            # A statically proven read-only command behaves like a read tool for this call.
            capability = {
                **capability,
                "read_only": True,
                "destructive": False,
                "side_effect_scope": "none",
                "risk_level": "low",
                "needs_approval": False,
                "permission_name": "read",
            }
    policy = config or PermissionConfig()
    mode = (mode or "build").strip().lower()

    # 1. Denying rules always win, from either source.
    for ruleset in (project_rules, rules):
        if ruleset is None:
            continue
        rule = first_match(ruleset.deny, tool_name, args)
        if rule is not None:
            return _deny(
                "rule.deny",
                capability,
                f"{tool_name} is denied by a permission rule",
                code="denied_by_rule",
            )

    # 2. Explicit ask rules precede every allowing branch.
    for ruleset in (project_rules, rules):
        if ruleset is None:
            continue
        rule = first_match(ruleset.ask, tool_name, args)
        if rule is not None:
            return _ask(
                "rule.ask",
                capability,
                f"{tool_name} requires approval by a permission rule",
                session_grant_allowed=False,
            )

    # 3. Plan is a gate on top of the other modes; it never widens them.
    gate = _plan_gate(mode, capability)
    if gate is not None:
        return gate

    # 4. Tools that demand explicit approval survive yolo.
    if _is_true(capability.get("always_ask")):
        return _ask("tool.always_ask", capability, f"{tool_name} always requires approval")

    # 5. yolo skips prompts for everything that was not restricted above.
    if mode == "yolo":
        return _allow("mode.yolo", capability, "yolo mode skips approval prompts")

    # 6. Explicit allow rules.
    if rules is not None:
        rule = first_match(rules.allow, tool_name, args)
        if rule is not None:
            return _allow("rule.allow", capability, f"{tool_name} is allowed by a permission rule")

    # 7. Edit mode accepts the in-workspace edit surface.
    if (
        mode == "edit"
        and capability.get("permission_name") == "edit"
        and capability.get("side_effect_scope") == "workspace"
    ):
        return _allow("mode.edit.file_edit", capability, "Edit mode allows in-workspace file edits")

    # 8. Remaining modes share the capability-based evaluation.
    return _build_evaluation(mode, capability, policy)
