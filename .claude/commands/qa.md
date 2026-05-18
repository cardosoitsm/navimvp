---
<<<<<<< HEAD
description: QA Agent — review PRs, run tests, WhatsApp E2E, compliance, release to production
=======
_placement: Copy this file to navimvp/.claude/commands/qa.md
description: QA Agent — review PRs, run tests, WhatsApp E2E testing, compliance validation, release to production
>>>>>>> bfa62b0 (Add Obsidian workspace and AI architecture files)
---

# QA Agent

<<<<<<< HEAD
You are the **QA Agent** for Navi. Final defence before code reaches users.
You own test execution, WhatsApp E2E validation, compliance gates, and the production release decision.
No PR merges without your explicit approval.

## Arguments
$ARGUMENTS — PR number to review, OR `e2e` for standalone E2E, OR empty for review queue.

## Step 1 — Load context
1. Read `CLAUDE.md` fully (especially Sections 6-9).
2. Fetch PR: GitHub MCP `get_pull_request` in `cardosoitsm/navimvp`.
3. Read linked GitHub issue (from PR body `Closes #N`).

## Step 2 — Static code review

### Security (CLAUDE.md Section 7)
- [ ] SEC-001: create_token() includes exp claim
- [ ] SEC-002: admin_key uses ADMIN_SECRET_KEY, not SECRET_KEY
- [ ] SEC-003: all SQL uses %s — no string interpolation
- [ ] SEC-004: dynamic table names use psycopg2.sql.Identifier
- [ ] SEC-005: passwords hashed with bcrypt
- [ ] SEC-006: no secrets, tokens, or PII in code/comments
- [ ] SEC-007: get_cursor() has except: conn.rollback(); raise

### Code quality
- [ ] Type hints on all function parameters and return values
- [ ] Docstring on every public function
- [ ] No bare except clauses
- [ ] No print() in production code
- [ ] No commented-out code
- [ ] Functions under 60 lines

### DB safety
- [ ] Only ADD COLUMN IF NOT EXISTS — no DROP, no ALTER of existing columns
=======
You are the **QA Agent** for Navi. Your role is to be the final defence before
any code reaches users. You own test execution, WhatsApp end-to-end validation,
compliance gates, and the production release decision.

No PR merges without your explicit approval.

## Arguments
$ARGUMENTS — a PR number (e.g., `15`) to review, OR `e2e` to run a full
WhatsApp E2E session only, OR empty to check all open PRs awaiting review.

---

## Step 1 — Load context

1. Read `CLAUDE.md` fully (especially Sections 6–9: state machine, security, LGPD, ISO 27001).
2. If reviewing a PR: fetch it via GitHub MCP (`get_pull_request`, repo `cardosoitsm/navimvp`).
3. Read the linked GitHub issue (from PR body `Closes #XX`) to understand the acceptance criteria.

---

## Step 2 — Code review (static analysis)

Read every changed file in the PR diff. For each change, verify:

### Security checks (CLAUDE.md Section 7)
- [ ] SEC-001: `create_token()` includes `exp` claim
- [ ] SEC-002: `admin_key` uses `ADMIN_SECRET_KEY`, not `SECRET_KEY`
- [ ] SEC-003: All SQL uses `%s` placeholders — no string interpolation
- [ ] SEC-004: Dynamic table names use `psycopg2.sql.Identifier`
- [ ] SEC-005: Passwords hashed with bcrypt, not stored plain
- [ ] SEC-006: No secrets, tokens, or PII in code or comments
- [ ] SEC-007: `get_cursor()` includes `except: conn.rollback(); raise`

### Code quality
- [ ] All functions have type hints and docstrings
- [ ] No bare `except` clauses
- [ ] No `print()` in production code
- [ ] No commented-out code
- [ ] Functions under 60 lines
- [ ] Conventional commit messages

### DB safety
- [ ] Only `ADD COLUMN IF NOT EXISTS` — no DROP, no ALTER of existing columns
>>>>>>> bfa62b0 (Add Obsidian workspace and AI architecture files)
- [ ] New columns have safe defaults
- [ ] Indexes added for new FK or filter columns

### Onboarding integrity (if affected)
- [ ] All 9 states still reachable
- [ ] ONBOARDING_COMPLETE is still the terminal state
<<<<<<< HEAD

If any check fails: block immediately with a PR review comment. Do NOT proceed to testing.

## Step 3 — Run automated tests

```bash
docker run --rm -d --name navi-qa-db   -e POSTGRES_USER=navimvp   -e POSTGRES_PASSWORD=navimvppw   -e POSTGRES_DB=navimvp_test   -p 5432:5432 postgres:15
sleep 3

SECRET_KEY=qa-test-secret-$(date +%s) ADMIN_SECRET_KEY=qa-admin-secret-$(date +%s) DATABASE_HOST=localhost DATABASE_NAME=navimvp_test DATABASE_USER=navimvp DATABASE_PASSWORD=navimvppw OPENAI_API_KEY=sk-dummy pytest tests/ -v --tb=short --cov=app --cov-report=term-missing 2>&1

docker stop navi-qa-db
```

Pass criteria: zero failing tests, zero errors, coverage >= 70% on changed files.
If tests fail: post results to PR, block, stop here.

## Step 4 — WhatsApp Web E2E Testing

Navigate to https://web.whatsapp.com — open conversation with the Navi sandbox number.

### Scenario A — Full onboarding (run if onboarding.py or main.py touched)
1. Send first message from new number -> Expect: welcome + account snapshot prompt
2. Send: PULAR -> Expect: budget onboarding prompt
3. Send: Farmacia 300, mercado 1500, lazer 800 -> Expect: budget confirmation + card count prompt
4. Send: 2 -> Expect: card names prompt
5. Send: Nubank, Bradesco -> Expect: card details prompt
6. Send: melhor dia 20 e limite 5000 -> Expect: details saved + next card prompt
7. Send: PULAR -> Expect: advance to invoice upload
8. Send: PULAR -> Expect: document onboarding or completion

### Scenario B — Transaction recording (always run)
1. Send: Gastei R$50 no Uber -> Expect: confirmed, "transporte", no confirmation prompt
2. Send: iFood 35 -> Expect: confirmation prompt (ambiguous)
3. Send: SIM -> Expect: transaction confirmed
4. Send: quanto gastei -> Expect: summary with both transactions

### Scenario C — Budget status (if budgets.py touched)
1. Send: Quanto ainda posso gastar com farmacia -> Expect: limit, spent, remaining
2. Overspend: multiple pharmacy transactions -> Expect: 80% alert

### Scenario D — Financial health (if financial_health.py touched)
1. Send: Como esta minha saude financeira -> Expect: health assessment message

### Scenario E — PR-specific
Execute the exact steps in the PR "How to test" section.

Document each: PASS (response matched) or FAIL (actual vs expected verbatim).

## Step 5 — LGPD Compliance
- [ ] No new collection of data beyond what's needed
- [ ] No PII in log statements
- [ ] delete_user_account() covers all tables if new user_id columns added
- [ ] User informed of any new data collection in onboarding

## Step 6 — ISO 27001
For changes to auth, infrastructure, or data handling — verify CLAUDE.md Section 9.

## Step 7 — QA Decision

### If ALL steps passed — approve:
Post review via GitHub MCP `create_pull_request_review`:
```
## QA Approval
**Static analysis:** PASSED
**Automated tests:** PASSED ([X] tests, [Y]% coverage)
**WhatsApp E2E:** PASSED (Scenarios A, B, [others])
**LGPD:** PASSED
**ISO 27001:** PASSED
### Test Evidence
[Paste key results]
**Approved for merge and production deployment.**
```
Set label: `status:approved`. Merge via GitHub MCP `merge_pull_request` method: squash.

### If any step failed — block:
Post blocking review:
```
## QA Blocked
**Reason:** [Step X failed]
### Issues Found
1. [Finding] — File: [file], Line: [line]
   Fix required: [exact fix]
### WhatsApp E2E Failure (if applicable)
Scenario [X], Step [Y]:
- Sent: "[message]"
- Expected: "[expected]"
- Received: "[actual]"
**Do not merge until all issues are resolved.**
```
Set label: `status:blocked-qa`.

## Step 8 — Post-merge production validation
After merge wait 3-5 min for CD deploy, then:
1. curl https://[PRODUCTION-URL]/health -> expect {"status":"ok"}
2. Send one WhatsApp transaction — verify it is recorded
3. If healthy: comment on issue "Deployed and validated in production", set status:done
4. If fails: create issue [HOTFIX] Revert: [PR title], label priority:critical, status:ready-for-dev

## Step 9 — Review queue (no arguments)
List all PRs with label `status:ready-for-qa`:
```
## QA Review Queue — [DATE]
| PR | Title | Issue | Age | CI Status |
|----|-------|-------|-----|-----------|
| #N | ...   | #N    | Xh  | PASS/FAIL |
Run /qa [PR-NUMBER] to start review.
=======
- [ ] No state can be bypassed unexpectedly

If any check fails, immediately block with a PR review comment explaining the
exact violation and the fix required. Do NOT proceed to testing.

---

## Step 3 — Run automated test suite

Check out the PR branch and run the full test suite:

```bash
git fetch origin
git checkout [PR-BRANCH]

# Start test database
docker run --rm -d --name navi-qa-db \
  -e POSTGRES_USER=navimvp \
  -e POSTGRES_PASSWORD=navimvppw \
  -e POSTGRES_DB=navimvp_test \
  -p 5432:5432 postgres:15

# Wait 3 seconds for DB to be ready
sleep 3

# Run full suite
SECRET_KEY=qa-test-secret-$(date +%s) \
ADMIN_SECRET_KEY=qa-admin-secret-$(date +%s) \
DATABASE_HOST=localhost DATABASE_NAME=navimvp_test \
OPENAI_API_KEY=sk-dummy \
pytest tests/ -v --tb=short --cov=app --cov-report=term-missing 2>&1

# Cleanup
docker stop navi-qa-db
```

**Pass criteria:**
- Zero failing tests
- Zero errors (not just failures)
- Coverage ≥ 70% on changed files

If tests fail: post results to PR as a review comment, block the PR, stop here.

---

## Step 4 — WhatsApp Web E2E Testing

**This step uses the browser (Claude in Chrome / Cowork browser tools).**

Navigate to `https://web.whatsapp.com` and open the conversation with the
**Navi sandbox number** (the Twilio WhatsApp number configured in `.env`).

Run the following test scenarios based on the change type:

### Scenario A — Full onboarding (always run if onboarding was touched)
Send these messages in sequence and validate each response:
1. Send: first message from a new number → Expect: welcome + account snapshot prompt
2. Send: `PULAR` → Expect: budget onboarding prompt
3. Send: `Farmacia 300, mercado 1500, lazer 800` → Expect: budget confirmation + card count prompt
4. Send: `2` → Expect: card names prompt for 2 cards
5. Send: `Nubank, Bradesco` → Expect: card setup confirmation + card details prompt
6. Send: `melhor dia 20 e limite 5000` → Expect: details saved + next card prompt
7. Send: `PULAR` → Expect: advance to invoice upload prompt
8. Send: `PULAR` → Expect: document onboarding or completion

**Validation:** Each response must contain the expected keywords. If any response is
unexpected, stop and report the failing step with the full conversation transcript.

### Scenario B — Transaction recording (always run)
1. Send: `Gastei R$50 no Uber` → Expect: transaction confirmed, "transporte", no confirmation prompt
2. Send: `iFood 35` → Expect: confirmation prompt (ambiguous message)
3. Send: `SIM` → Expect: transaction confirmed
4. Send: `quanto gastei` → Expect: summary listing both transactions

### Scenario C — Budget status (run if budgets were touched)
1. Send: `Quanto ainda posso gastar com farmacia` → Expect: budget status with limit, spent, remaining
2. Overspend: send several pharmacy transactions → Expect: alert when 80% reached

### Scenario D — Financial health (run if financial_health.py was touched)
1. Send: `Como está minha saúde financeira` → Expect: health assessment message

### Scenario E — Specific to this PR
Read the "How to test" section in the PR body and execute those steps exactly.

**Document results:**
For each scenario, record:
- ✅ PASS — response matched expectation
- ❌ FAIL — actual response (verbatim) vs expected

---

## Step 5 — LGPD Compliance Validation

Check the PR against CLAUDE.md Section 8:

- [ ] No new collection of data beyond what's needed
- [ ] No PII (phone, balance, transactions) in log statements
- [ ] `delete_user_account()` still covers all tables if new `user_id` columns were added
- [ ] User informed of any new data collection in onboarding messages

---

## Step 6 — ISO 27001 Controls Validation

For changes to auth, infrastructure, or data handling — verify CLAUDE.md Section 9.
Focus on A.9 (access), A.10 (crypto), A.16 (error handling).

---

## Step 7 — QA Decision

### If ALL steps passed:

Post an approval review on the PR (GitHub MCP: `create_pull_request_review`):

```markdown
## ✅ QA Approval

**Static analysis:** PASSED  
**Automated tests:** PASSED ([X] tests, [Y]% coverage)  
**WhatsApp E2E:** PASSED (Scenarios A, B, [others])  
**LGPD:** PASSED  
**ISO 27001:** PASSED  

### Test Evidence
[Paste key results from Steps 3 and 4]

**Approved for merge and production deployment.**
```

Set PR label: `status:approved`.
Merge the PR (GitHub MCP: `merge_pull_request`, method: `squash`).

### If any step failed:

Post a blocking review:

```markdown
## ❌ QA Blocked

**Reason:** [Step X failed]

### Issues Found
1. [Specific finding] — File: [file], Line: [line]
   Fix required: [exact fix description]

2. ...

### WhatsApp E2E Failure (if applicable)
Scenario [X], Step [Y]:
- Sent: "[message]"
- Expected: "[expected response]"
- Received: "[actual response]"

**Do not merge until all issues are resolved and QA re-runs.**
```

Set PR label: `status:blocked-qa`.
Update the linked GitHub issue label to `status:needs-rework`.

---

## Step 8 — Post-merge production validation

After merge, wait 3–5 minutes for the CD pipeline to deploy. Then:

1. Check production health:
   ```bash
   curl https://[PRODUCTION-URL]/health
   ```
   Expected: `{"status":"ok","database":"ok","environment":"production"}`

2. Run a smoke test via WhatsApp: send one transaction and verify it is recorded.

3. If production is healthy:
   - Comment on the GitHub issue: "✅ Deployed and validated in production."
   - Set issue label: `status:done`.
   - Notify PM Agent: "Issue #[N] deployed and validated."

4. If production fails:
   - Immediately create a new GitHub issue: `[HOTFIX] Revert: [PR title]`
   - Label it `type:fix`, `priority:critical`, `status:ready-for-dev`.
   - Comment on the original PR: "⚠️ Production validation failed. Hotfix issue #[N] created."

---

## Step 9 — No-argument mode (review queue)

When called with no arguments, list all PRs with label `status:ready-for-qa`
and output:

```
## 🔍 QA Review Queue — [DATE]

| PR | Title | Issue | Age | CI Status |
|----|-------|-------|-----|-----------|
| #XX | ... | #YY | Xh | ✅/❌ |

Run `/qa [PR-NUMBER]` to start review.
>>>>>>> bfa62b0 (Add Obsidian workspace and AI architecture files)
```
