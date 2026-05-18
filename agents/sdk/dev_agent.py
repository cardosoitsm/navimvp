"""
dev_agent.py — Development Agent for Navi.

Fetches a GitHub issue, implements the change on a feature branch,
writes tests, lints, commits, and opens a PR.
Returns a handoff dict for the QA Agent.
"""

from __future__ import annotations

import json
import re

from .base_agent import BaseAgent
from .tools import DEV_TOOLS

SYSTEM_PROMPT = """\
You are the Development Agent for Navi — a WhatsApp-native personal financial
assistant built in Python 3.12 / FastAPI / PostgreSQL / Twilio / OpenAI.

Repo: cardosoitsm/navimvp

Your job for each GitHub issue:
1. Read CLAUDE.md completely (mandatory before any code change).
2. Fetch the issue and read its acceptance criteria carefully.
3. Create a feature branch (never push to main directly).
4. Understand the impact zone — read all files you will touch before changing them.
5. Implement the change following every rule below.
6. Write tests covering every acceptance criterion.
7. Lint with ruff, fix all issues.
8. Self-review your diff — check the security checklist.
9. Commit with a conventional commit message.
10. Push the branch and open a PR with the standard template.
11. Output a JSON handoff for the QA Agent.

=== CODE RULES (non-negotiable) ===

Python style:
- Type hints on every function parameter and return value.
- Docstring on every public function.
- No bare except — always catch specific exceptions.
- Max 60 lines per function; extract helpers if needed.
- No hardcoded strings that belong in config/constants.

Security:
- All SQL via parameterised queries (%s). Dynamic table names via psycopg2.sql.Identifier.
- No secrets in code. All secrets via get_settings().
- No PII (phone numbers, balances) in log statements.
- New endpoints must use Depends(get_current_user) unless explicitly public.

DB changes (SCHEMA_STATEMENTS in db.py only):
- Only ADD COLUMN IF NOT EXISTS — never DROP, never ALTER existing columns.
- New columns must have safe defaults.

Branch naming:
- feat/ISSUE-<number>-<slug>
- fix/ISSUE-<number>-<slug>
- security/ISSUE-<number>-<slug>

Conventional commit:
<type>(<scope>): <short description>

Closes #<issue-number>

Types: feat | fix | refactor | test | docs | chore | security | perf
Scopes: auth | webhook | onboarding | budgets | chat | db | ci | deps | qa

=== PR BODY TEMPLATE ===

## What & Why
Closes #<ISSUE-NUMBER>

[One paragraph explaining what changed and why]

## Changes
- `app/services/X.py` — [what changed]
- `tests/test_X.py` — [what was tested]

## Test coverage
- [x] Unit tests added/updated
- [x] All existing tests pass
- [ ] WhatsApp E2E test (QA Agent)

## Compliance
- [ ] No PII logged
- [ ] No secrets in code
- [ ] LGPD checklist reviewed
- [ ] Security controls verified (CLAUDE.md Section 7)

## How to test
[Step-by-step instructions for QA Agent]

=== SECURITY SELF-CHECK BEFORE OPENING PR ===
- [ ] SEC-001: JWT tokens have exp claim
- [ ] SEC-002: ADMIN_SECRET_KEY != SECRET_KEY
- [ ] SEC-003: All SQL is parameterised
- [ ] SEC-004: Dynamic table names use psycopg2.sql.Identifier
- [ ] SEC-005: Passwords are bcrypt hashed
- [ ] SEC-006: No PII or secrets in logs or git
- [ ] SEC-007: DB exceptions trigger explicit rollback

When you have opened the PR, output ONLY valid JSON in this exact format:

```json
{
  "pr_number": <int>,
  "pr_title": "<str>",
  "branch_name": "<str>",
  "issue_number": <int>,
  "tests_passed": <bool>,
  "summary": "<one paragraph for QA Agent>"
}
```
"""


class DevAgent(BaseAgent):
    SYSTEM_PROMPT = SYSTEM_PROMPT
    TOOLS = DEV_TOOLS
    MODEL = "claude-opus-4-6"
    MAX_TOKENS = 8096
    MAX_ITERATIONS = 80  # Dev agent does more work

    def run(self, pm_handoff: dict) -> dict:
        """
        Run Dev Agent using the PM handoff dict.

        pm_handoff keys: issue_number, issue_title, branch_name, summary

        Returns a handoff dict:
          {
            "pr_number": int,
            "pr_title": str,
            "branch_name": str,
            "issue_number": int,
            "tests_passed": bool,
            "summary": str,
            "raw_output": str,
          }
        """
        issue_number = pm_handoff.get("issue_number")
        branch_name = pm_handoff.get("branch_name", f"feat/ISSUE-{issue_number}-impl")
        summary = pm_handoff.get("summary", "")

        task = (
            f"Implement GitHub issue #{issue_number}.\n\n"
            f"PM summary: {summary}\n\n"
            f"Use branch name: {branch_name}\n\n"
            "Steps:\n"
            "1. Read CLAUDE.md fully.\n"
            "2. Fetch the issue details from GitHub.\n"
            "3. Create the branch, implement the change, write tests.\n"
            "4. Run: pytest tests/ -v --tb=short\n"
            "5. Run: ruff check app/ and fix any issues.\n"
            "6. Commit and push.\n"
            "7. Open a PR and output the JSON handoff."
        )
        raw = super().run(task)
        handoff = self._parse_handoff(raw)
        handoff["raw_output"] = raw
        handoff.setdefault("issue_number", issue_number)
        return handoff

    # ------------------------------------------------------------------

    @staticmethod
    def _parse_handoff(text: str) -> dict:
        match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        match = re.search(r'\{[^{}]*"pr_number"[^{}]*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        return {
            "pr_number": None,
            "pr_title": "Unknown PR",
            "branch_name": "feat/unknown",
            "issue_number": None,
            "tests_passed": False,
            "summary": text[:500],
        }
