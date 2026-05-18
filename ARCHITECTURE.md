# Navi — Multi-Agent Architecture

> **Version:** 1.0  
> **Last updated:** 2026-05-03  
> **Scope:** Three-agent development system (PM · Dev · QA) running on Claude Code + GitHub + Azure

---

## Table of Contents

1. [Overview](#1-overview)
2. [Infrastructure Map](#2-infrastructure-map)
3. [Agent Responsibilities](#3-agent-responsibilities)
4. [End-to-End Workflow](#4-end-to-end-workflow)
5. [GitHub Branching Strategy](#5-github-branching-strategy)
6. [CI/CD Pipeline](#6-cicd-pipeline)
7. [WhatsApp E2E Testing Strategy](#7-whatsapp-e2e-testing-strategy)
8. [Compliance Gates](#8-compliance-gates)
9. [First-Time Setup](#9-first-time-setup)
10. [GitHub Secrets Reference](#10-github-secrets-reference)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. Overview

Navi uses three autonomous Claude Code agents to deliver features from idea to production
without manual hand-offs. Each agent has a single concern and communicates through GitHub
artifacts (issues, PRs, review comments, labels).

```
Requirement
    │
    ▼
┌──────────┐   GitHub Issue   ┌──────────┐   PR + CI green     ┌──────────┐
│ PM Agent │ ───────────────► │ Dev Agent│ ─────────────────►  │ QA Agent │
│  /pm     │                  │  /dev    │                     │  /qa     │
└──────────┘                  └──────────┘                     └──────────┘
                                                                     │
                                                              Merge + CD
                                                                     │
                                                                     ▼
                                                            Azure Production
```

All three agents share a single ground truth: **`CLAUDE.md`** at the repository root.
Every decision they make is traceable to a GitHub issue or PR comment.

---

## 2. Infrastructure Map

### GitHub (github.com/cardosoitsm/navimvp)

| Resource | Purpose |
|---|---|
| `main` branch | Production-ready code. Protected — no direct push. |
| Feature branches | `feat/ISSUE-N-slug`, `fix/ISSUE-N-slug`, etc. |
| Issues | Authoritative source of requirements and work items |
| PRs | Code review and QA gate — nothing merges without QA approval |
| GitHub Actions | CI (test + security + lint + docker-build) and CD (build → push → deploy) |
| Codespaces | Dev environment where Claude Code agents run |
| Environments | `production` — protects the deploy-prod workflow |

### Azure

| Resource | Purpose |
|---|---|
| `navimvp-hom-rg` | Resource Group containing all Navi infrastructure |
| `navimvpacr461e8d08` | Azure Container Registry — Docker image storage |
| `navimvp-hom-vm` | Azure VM running Docker Compose in production |
| `/opt/navi/` | App directory on the VM |
| `docker-compose.prod.yml` | Production compose file on the VM |

### Image Lifecycle

```
GitHub PR merge to main
        │
        ▼
GitHub Actions: build Docker image
        │
        ├─ Tags: latest + SHORT_SHA (first 8 chars of commit hash)
        │
        ▼
Push to navimvpacr461e8d08.azurecr.io/navimvp
        │
        ▼
az vm run-command invoke → docker-compose pull + up -d
        │
        ▼
Health check: GET /health → {"status":"ok"}
```

---

## 3. Agent Responsibilities

### PM Agent (`/pm`)

The PM Agent translates product requirements into actionable GitHub issues.
It never writes production code.

**Inputs:** Natural language requirement, user story, or bug report  
**Outputs:** GitHub issue with acceptance criteria, test requirements, compliance gates

**Responsibilities:**
- Analyse requirements against SPEC.md and CLAUDE.md
- Detect duplicate issues before creating new ones
- Classify LGPD and security implications
- Estimate complexity (S/M/L)
- Set initial labels (`type:feat`, `status:ready-for-dev`, `size:S`)
- Monitor sprint health and flag stale issues
- Close issues after QA confirms production validation

**Does NOT:** Write code, merge PRs, create branches, touch infrastructure

---

### Dev Agent (`/dev`)

The Dev Agent implements a single GitHub issue per invocation.
It always works on a feature branch and opens a PR when done.

**Inputs:** GitHub issue number (e.g., `/dev ISSUE-42`)  
**Outputs:** Feature branch + PR targeting `main` with label `status:ready-for-qa`

**Responsibilities:**
- Read CLAUDE.md before touching any code
- Create branch with correct naming convention
- Map impact zone before writing a single line
- Write implementation with type hints, docstrings, parameterised SQL
- Write tests covering every acceptance criterion
- Run `ruff check` and self-review checklist
- Commit with conventional commit messages
- Open PR with full template (What & Why, Changes, Compliance checklist)
- Respond to QA feedback on the same branch

**Does NOT:** Push to `main`, skip tests, ignore QA review comments

---

### QA Agent (`/qa`)

The QA Agent is the final gate before any code reaches users.
Its approval is required for every merge.

**Inputs:** PR number (e.g., `/qa 15`), or `e2e` for standalone testing, or empty for queue  
**Outputs:** GitHub PR review (approve/block) + merge decision + post-deploy validation

**Responsibilities:**
- Static code analysis against 7 security rules (SEC-001 to SEC-007)
- Code quality checklist (type hints, no bare except, function size)
- DB safety checklist (no DROP, ADD COLUMN IF NOT EXISTS)
- Onboarding state machine integrity
- Run automated test suite locally (pytest + coverage ≥ 70%)
- WhatsApp Web E2E testing via browser automation
- LGPD compliance validation
- ISO 27001 controls check
- Squash-merge approved PRs
- Post-merge production health validation
- Create hotfix issue if production fails post-deploy

**Does NOT:** Approve PRs with failing tests, ignore security rule violations

---

## 4. End-to-End Workflow

### Happy Path

```
1. User/PM identifies requirement
   └─ Run: /pm "Add income tracking for users"

2. PM Agent creates GitHub Issue #42
   └─ Label: type:feat, status:ready-for-dev, size:M
   └─ Body: acceptance criteria, test requirements, compliance gates

3. Dev Agent picks up issue
   └─ Run: /dev ISSUE-42
   └─ Creates branch: feat/ISSUE-42-income-tracking
   └─ Updates issue label → status:in-progress

4. Dev Agent implements and opens PR
   └─ All tests passing locally
   └─ Ruff clean
   └─ PR #17 opened targeting main
   └─ Label: status:ready-for-qa

5. GitHub Actions CI runs on PR
   └─ pytest (coverage ≥ 65%)
   └─ bandit (no HIGH severity)
   └─ pip-audit (no CVEs)
   └─ ruff check
   └─ docker build + health check
   └─ ✅ all-checks-passed gate

6. QA Agent reviews PR
   └─ Run: /qa 17
   └─ Static analysis: all 7 SEC checks pass
   └─ Automated tests: runs locally, coverage 78%
   └─ WhatsApp E2E: Scenarios A, B, E pass
   └─ LGPD: no new PII collection
   └─ Posts approval review + squash-merges PR

7. GitHub Actions CD runs on main push
   └─ Builds image → navimvpacr461e8d08.azurecr.io/navimvp:a1b2c3d4
   └─ Deploys to navimvp-hom-vm via az vm run-command invoke
   └─ Health check loop: 30 attempts × 3s
   └─ Public endpoint verified: GET /health → {"status":"ok"}

8. QA Agent post-deploy validation
   └─ Confirms production health
   └─ Sends smoke test WhatsApp message
   └─ Comments on issue: "✅ Deployed and validated in production"
   └─ Sets issue label → status:done

9. PM Agent closes issue
   └─ Adds compliance note if LGPD/security changes
```

### Blocked Path (QA rejects)

```
QA Agent posts blocking review
└─ Label: status:blocked-qa
└─ Lists exact findings with file + line numbers

Dev Agent reads review comments
└─ Creates fix commits on SAME branch
└─ Replies to each review comment when addressed
└─ Pushes → CI re-runs automatically

QA Agent re-reviews
└─ Run: /qa 17 (same PR number)
```

---

## 5. GitHub Branching Strategy

### Branch Naming

| Issue type | Branch prefix | Example |
|---|---|---|
| `type:feat` | `feat/ISSUE-N-slug` | `feat/ISSUE-42-income-tracking` |
| `type:fix` | `fix/ISSUE-N-slug` | `fix/ISSUE-43-token-expiry` |
| `type:security` | `security/ISSUE-N-slug` | `security/ISSUE-44-admin-key` |
| `type:test` | `test/ISSUE-N-slug` | `test/ISSUE-45-e2e-coverage` |
| `type:chore` | `chore/ISSUE-N-slug` | `chore/ISSUE-46-deps-update` |

Slug = issue title lowercased, spaces → hyphens, max 40 characters.

### Branch Protection Rules (configure in GitHub)

- `main` branch: require PR, require `all-checks-passed` status check, require 1 review approval
- No force push to `main`
- No direct push by anyone (including admins)
- Delete head branches after merge

### Label Schema

| Label | Meaning |
|---|---|
| `type:feat` | New feature |
| `type:fix` | Bug fix |
| `type:security` | Security improvement |
| `type:test` | Test-only change |
| `type:chore` | Maintenance (deps, CI, docs) |
| `status:ready-for-dev` | Issue available for Dev Agent |
| `status:in-progress` | Dev Agent actively working |
| `status:ready-for-qa` | PR opened, CI passing, awaiting QA |
| `status:approved` | QA approved, merge initiated |
| `status:blocked-qa` | QA found issues, needs rework |
| `status:done` | Deployed and validated in production |
| `size:S` | < 4 hours |
| `size:M` | 1–2 days |
| `size:L` | > 2 days |
| `priority:critical` | Incident / immediate action required |

---

## 6. CI/CD Pipeline

### CI Pipeline (`.github/workflows/ci.yml`)

Triggered on: PR to `main`, push to `main`

```
ci.yml
├── test          — pytest + PostgreSQL service + coverage ≥ 65%
├── security      — bandit (HIGH severity gate) + pip-audit (CVE gate)
├── lint          — ruff check app/
├── docker-build  — build + start container + health endpoint must respond
└── all-checks-passed  — fan-in gate (required status check for merge)
```

All four jobs run in parallel. The `all-checks-passed` job acts as the single required
status check for branch protection. This means adding new CI jobs doesn't require
updating branch protection rules — only `all-checks-passed` needs to be listed.

### CD Pipeline (`.github/workflows/deploy-prod.yml`)

Triggered on: push to `main`, `workflow_dispatch` (manual)

```
deploy-prod.yml
├── build-and-push
│   ├── Login to Azure (service principal)
│   ├── Login to ACR
│   ├── Build Docker image
│   ├── Tag: latest + SHORT_SHA
│   └── Push both tags to ACR
│
├── deploy  [needs: build-and-push, environment: production]
│   ├── Login to Azure
│   ├── az vm run-command invoke:
│   │   ├── cd /opt/navi
│   │   ├── az acr login
│   │   ├── docker-compose pull app
│   │   ├── docker-compose up -d --no-deps app
│   │   └── Health check loop (30 × 3s)
│   └── Verify public endpoint (5 × 15s)
│
└── notify  [if: failure()]
    └── Create GitHub Issue: [INCIDENT] Production deploy failed
        Labels: type:fix, priority:critical, status:ready-for-dev
```

The `environment: production` gate on the deploy job allows adding manual approval
rules in GitHub (Settings → Environments → production) without changing the workflow file.

### Concurrency Control

- CI: `cancel-in-progress: true` — new push cancels outdated CI run for the same branch
- CD: `cancel-in-progress: false` — production deploys never cancel; queue instead

---

## 7. WhatsApp E2E Testing Strategy

The QA Agent uses browser automation (Claude in Chrome / Cowork) to test the actual
WhatsApp conversation flow against the live sandbox number.

### Why WhatsApp Web (not unit mocks)

The Twilio webhook, FastAPI routing, OpenAI classification, and database writes all
interact in ways that unit tests cannot catch. WhatsApp Web E2E validates the complete
request path including:
- Twilio signature validation (if enabled)
- Webhook parsing (`From`, `Body` fields)
- Intent detection (`is_transaction`, `is_query`, `is_confirmation`)
- OpenAI response formatting
- State machine transitions
- Database persistence
- Brazilian Portuguese response quality

### Test Scenarios

| Scenario | When to run | What it covers |
|---|---|---|
| A — Full onboarding | If `onboarding.py` or `main.py` touched | All 9 states of onboarding state machine |
| B — Transaction recording | Always | Normal + ambiguous transactions, confirmation flow |
| C — Budget status | If `budgets.py` touched | Budget remaining, 80% alert trigger |
| D — Financial health | If `financial_health.py` touched | Health assessment message |
| E — PR-specific | Always | Steps from PR "How to test" section |

### Sandbox Setup

The QA Agent uses the **Twilio sandbox number** configured in `.env` as `TWILIO_NUMBER`.
To receive messages during testing, the sandbox phone must be joined to the Twilio
sandbox (send "join [sandbox-word]" once).

The QA Agent sends messages TO the app by typing in WhatsApp Web. The app responds
via the Twilio webhook. The QA Agent reads the response in WhatsApp Web and validates
it against the expected keywords.

### Pass Criteria

Each message exchange must produce a response within 30 seconds containing the
expected keywords. An unexpected response or timeout is an immediate FAIL — the QA
Agent stops, records the failing step verbatim, and posts a blocking review.

---

## 8. Compliance Gates

Every PR goes through two compliance validations before QA approval:

### LGPD (Lei Geral de Proteção de Dados)

| Gate | Check |
|---|---|
| Data minimisation | No new data collection beyond what's stated in the issue |
| PII in logs | No phone numbers, balances, or transaction details in log output |
| Right to erasure | `delete_user_account()` covers all tables if new `user_id` columns added |
| User consent | Any new data collection must be communicated in onboarding messages |

### ISO 27001 (Selected Controls)

| Control | Area | Check |
|---|---|---|
| A.9 | Access control | `Depends(get_current_user)` on all private endpoints |
| A.9 | Privilege separation | Admin key (`ADMIN_SECRET_KEY`) separate from JWT key (`SECRET_KEY`) |
| A.10 | Cryptography | JWT tokens include `exp` claim; passwords hashed with bcrypt |
| A.16 | Incident management | `get_cursor()` includes rollback on exception |

### Security Rules (CLAUDE.md Section 7)

The QA Agent checks all 7 rules on every PR:

| Rule | Description |
|---|---|
| SEC-001 | `create_token()` must include `exp` claim |
| SEC-002 | Admin endpoints use `ADMIN_SECRET_KEY`, not `SECRET_KEY` |
| SEC-003 | All SQL uses `%s` placeholders — no f-strings or string concatenation |
| SEC-004 | Dynamic table names use `psycopg2.sql.Identifier` |
| SEC-005 | Passwords hashed with bcrypt, never stored in plain text |
| SEC-006 | No secrets, tokens, or PII in code comments or log statements |
| SEC-007 | `get_cursor()` includes `except: conn.rollback(); raise` |

---

## 9. First-Time Setup

### Step 1 — Clone the repository

```bash
git clone https://github.com/cardosoitsm/navimvp.git
cd navimvp
```

### Step 2 — Copy agent files to `.claude/`

The agent command files live in `agents/` with placement comments.
Copy them to the locations Claude Code expects:

```bash
# Create the .claude/commands directory if it doesn't exist
mkdir -p .claude/commands

# Copy agent commands
cp agents/commands/pm.md   .claude/commands/pm.md
cp agents/commands/dev.md  .claude/commands/dev.md
cp agents/commands/qa.md   .claude/commands/qa.md

# Copy Claude Code settings
cp agents/claude-settings.json .claude/settings.json
```

### Step 3 — Install the GitHub MCP server

Run this once in your Codespace or dev environment:

```bash
npm install -g @modelcontextprotocol/server-github
```

Verify installation:

```bash
npx @modelcontextprotocol/server-github --version
```

The `claude-settings.json` (copied to `.claude/settings.json`) already configures
this MCP server. Claude Code will pick it up automatically on next start.

### Step 4 — Configure GitHub Personal Access Token

The GitHub MCP needs a token to create issues, PRs, and reviews.

```bash
# In your Codespace or terminal, set the environment variable:
export GITHUB_TOKEN=ghp_your_token_here

# Or add it to your shell profile (~/.bashrc, ~/.zshrc):
echo 'export GITHUB_TOKEN=ghp_your_token_here' >> ~/.bashrc
```

**Required token permissions:**
- `repo` (full repository access)
- `read:org` (if using organization repo)

### Step 5 — Configure GitHub Actions Secrets

In the GitHub repository (Settings → Secrets and variables → Actions), create:

| Secret | Value | Used by |
|---|---|---|
| `AZURE_CREDENTIALS` | Azure service principal JSON (see below) | Both CI and CD |
| `CI_SECRET_KEY` | Random 32+ char string (test JWT key) | CI test job |
| `CI_ADMIN_SECRET_KEY` | Random 32+ char string (test admin key) | CI test job |
| `PRODUCTION_URL` | Your domain (e.g., `navi.yourdomain.com`) | CD health check |

**Generating `AZURE_CREDENTIALS`:**

```bash
az ad sp create-for-rbac \
  --name "navi-github-actions" \
  --role contributor \
  --scopes /subscriptions/YOUR_SUB_ID/resourceGroups/navimvp-hom-rg \
  --sdk-auth
```

Copy the full JSON output as the `AZURE_CREDENTIALS` secret.

**Generate `CI_SECRET_KEY` and `CI_ADMIN_SECRET_KEY`:**

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Run twice to get two different keys.

### Step 6 — Configure the GitHub Environment

In the repository (Settings → Environments), create an environment named `production`.

Recommended settings:
- Required reviewers: add yourself (optional — prevents accidental deploys)
- Deployment branches: `main` only

### Step 7 — Create GitHub Labels

Create these labels in the repository (Issues → Labels):

```bash
# Using GitHub CLI (gh)
gh label create "type:feat"           --color "0075ca" --repo cardosoitsm/navimvp
gh label create "type:fix"            --color "d73a4a" --repo cardosoitsm/navimvp
gh label create "type:security"       --color "e4e669" --repo cardosoitsm/navimvp
gh label create "type:test"           --color "cfd3d7" --repo cardosoitsm/navimvp
gh label create "type:chore"          --color "e4e669" --repo cardosoitsm/navimvp
gh label create "status:ready-for-dev"  --color "0e8a16" --repo cardosoitsm/navimvp
gh label create "status:in-progress"    --color "fbca04" --repo cardosoitsm/navimvp
gh label create "status:ready-for-qa"  --color "006b75" --repo cardosoitsm/navimvp
gh label create "status:approved"      --color "0e8a16" --repo cardosoitsm/navimvp
gh label create "status:blocked-qa"    --color "d73a4a" --repo cardosoitsm/navimvp
gh label create "status:done"          --color "cfd3d7" --repo cardosoitsm/navimvp
gh label create "priority:critical"    --color "b60205" --repo cardosoitsm/navimvp
gh label create "size:S"               --color "c2e0c6" --repo cardosoitsm/navimvp
gh label create "size:M"               --color "fef2c0" --repo cardosoitsm/navimvp
gh label create "size:L"               --color "f9d0c4" --repo cardosoitsm/navimvp
```

### Step 8 — Set up branch protection

```bash
# Using GitHub CLI
gh api repos/cardosoitsm/navimvp/branches/main/protection \
  --method PUT \
  --input - << 'EOF'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["All CI Checks Passed"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1
  },
  "restrictions": null
}
EOF
```

### Step 9 — Set up the production VM

SSH into the VM (or use `az vm run-command invoke`) and run:

```bash
# Create app directory
sudo mkdir -p /opt/navi
sudo chown $USER:$USER /opt/navi

# Copy docker-compose.prod.yml and .env to /opt/navi/
# Then pull and start for the first time:
cd /opt/navi
az acr login --name navimvpacr461e8d08
docker-compose -f docker-compose.prod.yml pull
docker-compose -f docker-compose.prod.yml up -d
```

After initial setup, all subsequent deployments are handled automatically by the
CD pipeline via `az vm run-command invoke`.

### Step 10 — Verify the setup

Start a new Claude Code session in the repository directory and run:

```
/pm
```

The PM Agent should load context from `CLAUDE.md`, read recent commits, and output
a sprint status report. If you see an error about GitHub MCP, verify `GITHUB_TOKEN`
is set and the MCP server is installed (Step 3).

---

## 10. GitHub Secrets Reference

| Secret | Required by | Description |
|---|---|---|
| `AZURE_CREDENTIALS` | `ci.yml`, `deploy-prod.yml` | Service principal JSON from `az ad sp create-for-rbac --sdk-auth` |
| `CI_SECRET_KEY` | `ci.yml` (test job) | JWT signing key for tests — any random 32+ char string |
| `CI_ADMIN_SECRET_KEY` | `ci.yml` (test job) | Admin key for tests — different from `CI_SECRET_KEY` |
| `PRODUCTION_URL` | `deploy-prod.yml` | Public domain for health check (no `https://` prefix) |

### Environment Variables on the VM (`/opt/navi/.env`)

| Variable | Description |
|---|---|
| `SECRET_KEY` | JWT signing key — minimum 32 characters, random |
| `ADMIN_SECRET_KEY` | Admin endpoint key — must differ from `SECRET_KEY` |
| `ALGORITHM` | `HS256` |
| `DATABASE_HOST` | PostgreSQL host (usually `db` for Docker Compose) |
| `DATABASE_PORT` | `5432` |
| `DATABASE_NAME` | `navimvp` |
| `DATABASE_USER` | `navimvp` |
| `DATABASE_PASSWORD` | Strong random password |
| `OPENAI_API_KEY` | OpenAI API key for gpt-4.1-mini |
| `ACCOUNT_SID` | Twilio Account SID |
| `AUTH_TOKEN` | Twilio Auth Token |
| `TWILIO_NUMBER` | `whatsapp:+55119XXXXXXXX` |

---

## 11. Troubleshooting

### CI fails: "Coverage X% is below minimum threshold of 65%"

The test coverage dropped. The Dev Agent must:
1. Identify which new code is not covered (`--cov-report=term-missing` shows exact lines)
2. Add tests for uncovered paths before re-pushing

### CD fails: "Application did not become healthy in 90 seconds"

The new image fails to start on the VM. Common causes:
1. Missing environment variable in `/opt/navi/.env`
2. Database migration failed (check `SCHEMA_STATEMENTS` in `db.py`)
3. Image not pushed to ACR before deploy step ran

Check VM logs: `docker-compose -f docker-compose.prod.yml logs --tail=100 app`

### GitHub MCP not working in Claude Code

1. Verify `GITHUB_TOKEN` is exported: `echo $GITHUB_TOKEN`
2. Verify MCP server is installed: `which mcp-server-github` or `npx @modelcontextprotocol/server-github --version`
3. Check `.claude/settings.json` exists and contains the `mcpServers` block
4. Restart Claude Code after any settings change

### QA Agent: WhatsApp Web not loading

The QA Agent uses Claude in Chrome / Cowork browser tools to interact with
`https://web.whatsapp.com`. If WhatsApp Web requires a QR code scan:
1. The user must scan the QR code once to link the browser session
2. After linking, the session persists across QA runs (no re-scan needed)
3. If the session expires, re-scan is required before the next E2E run

### Deploy creates incident issue but app is actually healthy

This is a false positive from the health check timing. The `notify` job fires if
`build-and-push` or `deploy` fails — it does not fire if the health check times out
with a healthy app. If you see a spurious incident issue:
1. Manually verify production health: `curl https://PRODUCTION_URL/health`
2. Close the issue with label `status:done` and a note explaining the false positive
3. Consider increasing the sleep time before the public health check in `deploy-prod.yml`

---

## File Reference

```
navimvp/
├── CLAUDE.md                    ← Root context for all agents (read first)
├── ARCHITECTURE.md              ← This file
├── SPEC.md                      ← Product vision and ICS definition
│
├── .github/
│   └── workflows/
│       ├── ci.yml               ← Test + security + lint + docker-build
│       └── deploy-prod.yml      ← Build → push → deploy → validate
│
├── agents/                      ← Agent source files (copy to .claude/)
│   ├── ARCHITECTURE.md          ← This file lives here in source
│   ├── claude-settings.json     ← Copy to .claude/settings.json
│   └── commands/
│       ├── pm.md                ← Copy to .claude/commands/pm.md
│       ├── dev.md               ← Copy to .claude/commands/dev.md
│       └── qa.md                ← Copy to .claude/commands/qa.md
│
├── app/
│   ├── main.py                  ← FastAPI routes + webhook orchestration
│   ├── auth.py                  ← JWT creation/validation
│   ├── config.py                ← Pydantic settings
│   ├── db.py                    ← PostgreSQL connection + SCHEMA_STATEMENTS
│   ├── schemas.py               ← Pydantic models
│   ├── Dockerfile               ← Production Docker image
│   └── services/
│       ├── users.py
│       ├── chat.py
│       ├── conversation.py
│       ├── budgets.py
│       ├── summary.py
│       ├── financial_health.py
│       ├── onboarding.py
│       ├── formatting.py
│       └── documents.py
│
├── tests/
│   ├── conftest.py              ← DB fixtures, auth helpers, OpenAI mocks
│   ├── test_auth.py
│   ├── test_health.py
│   ├── test_chat.py
│   ├── test_webhook.py
│   ├── test_unit_pure.py
│   └── test_security.py
│
├── docker-compose.yml           ← Local development
├── docker-compose.prod.yml      ← Production (on VM at /opt/navi/)
└── .env.example                 ← All required variables with descriptions
```
