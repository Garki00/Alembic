---
name: code-review
description: Review proposed code changes for concrete bugs, regressions, security boundaries, and missing validation. Use when the user requests a code review or an assessment of a patch before merging.
license: MIT
compatibility: Requires access to the proposed changes and relevant surrounding source files. Git is helpful but optional.
metadata:
  author: Alembic
  version: "1.0"
---
# Review code changes

Establish the intended behavior from the request and available project guidance.
Read the patch and the callers, data contracts, and tests needed to understand it.
Use the checklist in `references/review-checklist.md` when assessing risk.

Trace concrete inputs through changed behavior. Prioritize correctness, security,
data loss, compatibility, and observable regressions. Distinguish a demonstrated
problem from a speculative concern. Explain the trigger, consequence, and smallest
useful fix for each finding. Include file and line references when available.

Run focused existing checks when practical and authorized. Report exactly which
checks ran and any limits on verification. Do not execute a script merely because
it is included in a skill. Do not change source code unless the user asked for fixes.

Present findings by severity, followed by unresolved questions and verification.
If no actionable issue is found, say so plainly and describe remaining test gaps.
Do not inflate style preferences into correctness findings.
