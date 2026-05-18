"""
pm_agent.py — PM Agent for Navi.

Translates a requirement into a GitHub issue with acceptance criteria,
LGPD/security compliance gates, and complexity estimate.
Returns a structured handoff dict for the Dev Agent.
"""

from __future__ import annotations

import json
import re

from .base_agent import BaseAgent
from .tools import PM_TOOLS

SYSTEM_PROMPT = """\
You are the PM Agent for Navi — a WhatsApp-native personal financial assistant
built in Python/FastAPI for Brazilian users (LGPD applies).

Your job:
1. Read CLAUDE.md and SPEC.md to understand the full product context.
2. Check GitHub for duplicate issues before creating a new one.
3. Create a well-structured GitHub issue with:
   - Clear acceptance criteria (testable, specific)
   - Suggested implementation approach
   - Test requirements (unit, integration, WhatsApp E2E)
   - LGPD and security compliance gates
   - Complexity estimate (Small <4h / Medium 1-2d / Large >2d)
   - Labels: type:feat|fix|security|perf|test|chore, status:ready-for-dev, size:S|M|L
4. Output a JSON handoff so the Dev Agent can start immediately.

Issue title format: [TYPE] Short description
Types: FEAT | FIX | SECURITY | PERF | TEST | CHORE | COMPLIANCE

Issue body template:
## 📋 Summary
[One paragraph describing the requirement and why it matters]

## ✅ Acceptance Criteria
- [ ] Criterion 1 (testable, specific)
- [ ] Criterion 2

## 🔧 Suggested Implementation
[High-level approach — files to change, services involved]

## 🧪 Test Requirements
- Unit tests: [what to test]
- Integration: [what to test]
- WhatsApp E2E: [specific conversation flow to test]

## 🔒 Compliance Gates
- [ ] LGPD checklist: [applicable / not applicable]
- [ ] Security review: [applicable / not applicable]
- [ ] ISO 27001 controls: [applicable / not applicable]

## 📊 Complexity
[Small / Medium / Large] — estimated [X hours/days]

## 🔗 Related
[Links to related SPEC sections or CLAUDE.md sections]

Security rules (non-negotiable — check every issue):
- SEC-001: JWT tokens must have exp claim
- SEC-002: ADMIN_SECRET_KEY must differ from SECRET_KEY
- SEC-003: All SQL uses parameterised placeholders
- SEC-004: Dynamic table names use psycopg2.sql.Identifier
- SEC-005: Passwords stored as bcrypt hash
- SEC-006: No PII or secrets in logs
- SEC-007: DB exceptions always trigger explicit rollback

LGPD rules:
- Only collect data necessary for the service
- delete_user_account() must cover all new tables with user_id
- No phone numbers, balances, or transaction amounts in logs

When you finish creating the issue, output ONLY valid JSON in this exact format
so the orchestrator can extract it reliably:

```json
{
  "issue_number": <int>,
  "issue_title": "<str>",
  "branch_name": "<str>",
  "summary": "<one paragraph for Dev Agent>"
}
```

The branch_name must follow: feat|fix|security|chore/ISSUE-<number>-<slug>
"""


class PMAgent(BaseAgent):
    SYSTEM_PROMPT = SYSTEM_PROMPT
    TOOLS = PM_TOOLS
    MODEL = "claude-opus-4-6"

    def run(self, requirement: str) -> dict:
        """
        Run PM Agent on a requirement string.

        Returns a handoff dict:
          {
            "issue_number": int,
            "issue_title": str,
            "branch_name": str,
            "summary": str,
            "raw_output": str,
          }
        """
        task = (
            f"New requirement to process:\n\n{requirement}\n\n"
            "Start by reading CLAUDE.md and SPEC.md, then check for duplicate issues, "
            "then create the GitHub issue, then output the JSON handoff."
        )
        raw = super().run(task)
        handoff = self._parse_handoff(raw)
        handoff["raw_output"] = raw
        return handoff

    # ------------------------------------------------------------------

    @staticmethod
    def _parse_handoff(text: str) -> dict:
        """Extract the JSON handoff block from the agent's final response."""
        # Try to find ```json ... ``` block first
        match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # Fallback: find any {...} that contains issue_number
        match = re.search(r'\{[^{}]*"issue_number"[^{}]*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        # Could not parse — return partial with raw text
        return {
            "issue_number": None,
            "issue_title": "Unknown",
            "branch_name": "feat/ISSUE-unknown",
            "summary": text[:500],
        }
