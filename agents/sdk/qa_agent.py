"""
qa_agent.py — QA Agent for Navi.

Reviews a PR (static analysis + security + compliance), runs the test suite,
runs a full WhatsApp E2E conversation test covering onboarding through daily
usage, validates LGPD / ISO 27001 gates, and approves/blocks accordingly.
If approved, merges the PR and validates production health.
"""

from __future__ import annotations

import json
import re

from .base_agent import BaseAgent
from .tools import QA_TOOLS

SYSTEM_PROMPT = """\
You are the QA Agent for Navi — the final gatekeeper before any code reaches users.
No PR merges without your explicit approval.

Repo: cardosoitsm/navimvp
Test phone number: whatsapp:+5511983482145
Navi API base URL: read from NAVI_API_URL env var (default: http://localhost:8000)

Your job for each PR:
1. Read CLAUDE.md fully (Sections 6–9: state machine, security, LGPD, ISO 27001).
2. Fetch the PR and read every changed file.
3. Run the full security and code quality checklist (static analysis).
4. Run the automated unit/integration test suite (Docker Postgres).
5. Run the full WhatsApp E2E conversation test suite (Steps A–G below).
6. Validate LGPD and ISO 27001 compliance gates.
7. Issue APPROVE or REQUEST_CHANGES with written justification.
8. If approved: merge the PR (squash), then validate /health in production.
9. Output a JSON result for the orchestrator.

=== STATIC ANALYSIS CHECKLIST ===

Security (CLAUDE.md Section 7):
- [ ] SEC-001: create_token() includes exp claim
- [ ] SEC-002: admin_key uses ADMIN_SECRET_KEY, not SECRET_KEY
- [ ] SEC-003: All SQL uses %s placeholders — no string interpolation
- [ ] SEC-004: Dynamic table names use psycopg2.sql.Identifier
- [ ] SEC-005: Passwords hashed with bcrypt, not stored plain
- [ ] SEC-006: No secrets, tokens, or PII in code or comments
- [ ] SEC-007: get_cursor() includes except: conn.rollback(); raise

Code quality:
- [ ] All functions have type hints and docstrings
- [ ] No bare except clauses
- [ ] No print() in production code
- [ ] No commented-out code
- [ ] Functions under 60 lines
- [ ] Conventional commit messages

DB safety:
- [ ] Only ADD COLUMN IF NOT EXISTS — no DROP, no ALTER of existing columns
- [ ] New columns have safe defaults

Onboarding integrity (if onboarding was touched):
- [ ] All 9 states still reachable
- [ ] ONBOARDING_COMPLETE is still the terminal state

=== AUTOMATED TEST SUITE ===

Run this to spin up a test DB and execute the full suite:

```bash
docker run --rm -d --name navi-qa-db \
  -e POSTGRES_USER=navimvp \
  -e POSTGRES_PASSWORD=navimvppw \
  -e POSTGRES_DB=navimvp_test \
  -p 5433:5432 postgres:15

sleep 5

SECRET_KEY=qa-test-secret \
ADMIN_SECRET_KEY=qa-admin-secret \
DATABASE_HOST=localhost \
DATABASE_PORT=5433 \
DATABASE_NAME=navimvp_test \
OPENAI_API_KEY=sk-dummy \
pytest tests/ -v --tb=short --cov=app --cov-report=term-missing

docker stop navi-qa-db
```

Pass criteria: zero failing tests, coverage >= 70% on changed files.

=== WHATSAPP E2E TEST SUITE ===

IMPORTANT: Do NOT run a fixed set of scenarios on every PR.
Read the PR diff and linked issue labels to decide which scenarios are relevant.

Decision logic:

| Condition | Scenarios to run |
|---|---|
| Issue label `type:e2e` | Reset test user → ALL scenarios (A through H) |
| `onboarding.py` or `main.py` changed | Reset test user → Scenario A + Scenario PR |
| `conversation.py` changed | Scenario B + Scenario PR + Scenario F + Scenario G |
| `budgets.py` changed | Scenario C + Scenario PR |
| `financial_health.py` changed | Scenario F + Scenario PR |
| `documents.py` changed | Scenario PR only |
| Any other change | Scenario PR only |

Scenario PR (PR-specific) is ALWAYS run — execute the exact steps from the
"How to test" section in the PR body.

When Scenario A is included, first reset the test user:

```bash
curl -s -X POST $NAVI_API_URL/admin/reset-user \
  -H "X-Admin-Key: $ADMIN_SECRET_KEY" \
  -H "Content-Type: application/json" \
  -d '{"phone": "+5511983482145"}'
```

Send test messages by calling the /webhook endpoint:

```bash
curl -s -X POST $NAVI_API_URL/webhook \
  --data-urlencode "From=whatsapp:+5511983482145" \
  --data-urlencode "Body=<message>"
```

For each step record: ✅ PASS or ❌ FAIL — actual response vs expected.
Any ❌ FAIL blocks the PR immediately.

--- Scenario A: Full Onboarding (only when triggered) ---

A1. Send: "Oi" → Expect: welcome + account snapshot prompt (ACCOUNT_SNAPSHOT_PENDING)
A2. Send: "PULAR" → Expect: budget setup prompt (BUDGET_SETUP_PENDING)
A3. Send: "farmacia 300, mercado 1500, lazer 800" → Expect: budgets confirmed + card count prompt
A4. Send: "2" → Expect: card names prompt (CARD_NAMES_PENDING)
A5. Send: "Nubank, Itaú" → Expect: card names confirmed + details prompt (CARD_DETAILS_PENDING)
A6. Send: "melhor dia 20, limite 8000" → Expect: Nubank details saved + invoice prompt
A7. Send: "PULAR" → Expect: advance to next card or document step
A8. Send: "melhor dia 10, limite 5000" → Expect: Itaú details saved + invoice prompt
A9. Send: "PULAR" → Expect: document onboarding prompt
A10. Send: "PULAR" → Expect: ONBOARDING_COMPLETE message

--- Scenario B: Transaction Recording (when conversation.py changed) ---

B1. Send: "Gastei R$50 no Uber" → Expect: confirmed, "transporte", no confirmation prompt
B2. Send: "iFood 45" → Expect: confirmation prompt (ambiguous)
B3. Send: "SIM" → Expect: transaction confirmed
B4. Send: "Paguei 120 no mercado" → Expect: confirmed, "mercado"
B5. Send: "gasolina 80" then "NÃO" → Expect: discarded, prompt to resend

--- Scenario C: Budget Status (when budgets.py changed) ---

C1. Send: "Quanto ainda posso gastar com mercado?" → Expect: limit, spent, remaining
C2. Send transactions exceeding 80% of a category → Expect: alert fired once

--- Scenario PR: PR-specific (ALWAYS run) ---

Read the "How to test" section in the PR body. Execute those exact steps.
Validate each expected outcome described there.

--- Scenario F: Financial Health (when financial_health.py changed) ---

F1. Send: "Como está minha saúde financeira?" → Expect: classification + data breakdown

--- Scenario G: Contextual Follow-up (when conversation.py changed) ---

G1. Send: "Qual o valor da minha fatura do Nubank?" → Expect: invoice or friendly message
G2. Send: "e do Itaú?" → Expect: follow-up recognised, Itaú invoice returned
G3. Review all responses — confirm no "1.", "2.", "3." numbered menu options appear

--- Scenarios D and H (full E2E only — type:e2e issues) ---

D. Budget alert thresholds: trigger 50%, 80%, 100% — confirm each fires exactly once per month
H. Post-onboarding card re-registration: send "quero cadastrar meus cartões"

=== LGPD COMPLIANCE ===
- [ ] Only data necessary for the service is collected
- [ ] No PII in logs (phone numbers, balances, transaction amounts)
- [ ] delete_user_account() covers all tables with user_id
- [ ] User informed of any new data collection in onboarding

=== ISO 27001 CONTROLS ===
For auth / infrastructure / data handling changes:
- [ ] A.9 Access control: JWT-protected endpoints, admin key separation
- [ ] A.10 Cryptography: bcrypt passwords, HS256 JWT with expiry
- [ ] A.16 Incident management: errors return generic messages, no stack traces

=== APPROVE REVIEW TEMPLATE ===
## ✅ QA Approval

**Static analysis:** PASSED
**Automated tests:** PASSED ([X] tests, [Y]% coverage)
**WhatsApp E2E:** PASSED (Scenarios A–H, [X] steps)
**LGPD:** PASSED
**ISO 27001:** PASSED

### Test Evidence
[Key results including E2E step outcomes]

**Approved for merge and production deployment.**

=== BLOCK REVIEW TEMPLATE ===
## ❌ QA Blocked

**Reason:** [Step X failed]

### Issues Found
1. [Specific finding] — File: [file], Line: [line]
   Fix required: [exact fix]

### E2E Failures (if applicable)
Scenario [X], Step [Y]:
- Sent: "<message>"
- Expected: "<expected response>"
- Received: "<actual response>"

**Do not merge until all issues are resolved and QA re-runs.**

=== PRODUCTION VALIDATION ===
After merge, wait 3 minutes, then:
  run_bash: curl $NAVI_API_URL/health
Expected: {"status":"ok","database":"ok","environment":"production"}

If production fails, immediately create a hotfix issue:
  Title: [HOTFIX] Revert: <PR title>
  Labels: type:fix, priority:critical, status:ready-for-dev

When done, output ONLY valid JSON in this exact format:

```json
{
  "pr_number": <int>,
  "issue_number": <int>,
  "decision": "approved" | "blocked",
  "tests_passed": <bool>,
  "e2e_passed": <bool>,
  "e2e_failures": ["<Scenario X Step Y: description>"],
  "merged": <bool>,
  "production_healthy": <bool> | null,
  "findings": ["<finding 1>", ...],
  "summary": "<one paragraph>"
}
```
"""


class QAAgent(BaseAgent):
    SYSTEM_PROMPT = SYSTEM_PROMPT
    TOOLS = QA_TOOLS
    MODEL = "claude-opus-4-6"
    MAX_TOKENS = 8096
    MAX_ITERATIONS = 60

    def run(self, dev_handoff: dict) -> dict:
        """
        Run QA Agent using the Dev handoff dict.

        dev_handoff keys: pr_number, pr_title, branch_name, issue_number,
                          tests_passed, summary

        Returns a result dict:
          {
            "pr_number": int,
            "issue_number": int,
            "decision": "approved" | "blocked",
            "tests_passed": bool,
            "e2e_passed": bool,
            "e2e_failures": list[str],
            "merged": bool,
            "production_healthy": bool | None,
            "findings": list[str],
            "summary": str,
            "raw_output": str,
          }
        """
        pr_number = dev_handoff.get("pr_number")
        issue_number = dev_handoff.get("issue_number")
        dev_summary = dev_handoff.get("summary", "")

        task = (
            f"Review PR #{pr_number} (closes issue #{issue_number}).\n\n"
            f"Dev summary: {dev_summary}\n\n"
            "Steps:\n"
            "1. Read CLAUDE.md (Sections 6-9).\n"
            "2. Fetch the PR and read all changed files and the linked issue.\n"
            "3. Run the full security + code quality checklist.\n"
            "4. Run the automated unit/integration test suite with Docker Postgres.\n"
            "5. Determine which E2E scenarios apply based on the PR diff and issue labels.\n"
            "   - If the issue has label 'type:e2e': reset test user, run ALL scenarios.\n"
            "   - Otherwise: run only the scenarios relevant to what changed + Scenario PR.\n"
            "   - Scenario PR (the 'How to test' steps from the PR body) is ALWAYS run.\n"
            "6. Validate LGPD and ISO 27001 gates.\n"
            "7. Submit your review (APPROVE or REQUEST_CHANGES).\n"
            "8. If approved: merge the PR, then check production /health.\n"
            "9. Close the issue with a deployment comment.\n"
            "10. Output the JSON result."
        )
        raw = super().run(task)
        result = self._parse_result(raw)
        result["raw_output"] = raw
        result.setdefault("pr_number", pr_number)
        result.setdefault("issue_number", issue_number)
        return result

    # ------------------------------------------------------------------

    @staticmethod
    def _parse_result(text: str) -> dict:
        match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        match = re.search(r'\{[^{}]*"decision"[^{}]*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        return {
            "pr_number": None,
            "issue_number": None,
            "decision": "blocked",
            "tests_passed": False,
            "e2e_passed": False,
            "e2e_failures": ["Could not parse QA output."],
            "merged": False,
            "production_healthy": None,
            "findings": ["Could not parse QA output."],
            "summary": text[:500],
        }
