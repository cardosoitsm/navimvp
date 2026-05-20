# Navi MVP — Claude Code Project Context

> **Read this file completely before taking any action in this repository.**
> Every Claude Code session, regardless of agent role, starts here.

---

## 1. What Is Navi

Navi is a WhatsApp-native financial assistant that helps users track spending,
understand cash flow, and build financial health — delivered entirely through
conversational messages. No app to install. No dashboard to learn.

- **ICS (Ideal Customer Segment):** Brazilian individuals who manage household
  finances informally and want lightweight, proactive guidance over WhatsApp.
- **Core promise:** "Controle financeiro sem complicação."
- **Interaction channel:** WhatsApp (Twilio webhook) + REST API for internal tools.
- **AI backbone:** OpenAI gpt-4.1-mini for transaction classification and document analysis.

---

## 2. Technology Stack

| Layer | Technology |
|---|---|
| Runtime | Python 3.12, FastAPI, Uvicorn |
| Database | PostgreSQL 15 |
| Auth | JWT (python-jose) + bcrypt (passlib) |
| AI | OpenAI SDK (gpt-4.1-mini) |
| Messaging | Twilio (WhatsApp webhook) |
| Container | Docker + Docker Compose |
| Registry | Azure Container Registry — `navimvpacr461e8d08.azurecr.io` |
| Hosting | Azure VM — `navimvp-hom-vm` (Ubuntu), Resource Group `navimvp-hom-rg` |
| CI/CD | GitHub Actions → Azure (repo: `cardosoitsm/navimvp`) |
| IaC | Azure — Subscription `Sub_Cardoso_01` |

### Key files
```
app/main.py              — FastAPI app, all routes, webhook orchestration
app/auth.py              — JWT creation/validation, password hashing
app/config.py            — Pydantic settings (env-driven)
app/db.py                — psycopg2 connection, schema migrations (SCHEMA_STATEMENTS)
app/schemas.py           — Pydantic request/response models
app/services/
  chat.py                — OpenAI transaction extraction + DB write
  conversation.py        — Intent detection, confirmation flow
  onboarding.py          — Full onboarding state machine (9 states)
  budgets.py             — Budget CRUD, alert engine
  summary.py             — Spending summaries, insights
  financial_health.py    — Financial health score builder
  documents.py           — Document upload, OCR, invoice parsing
  users.py               — User registration, WhatsApp auto-registration, delete
  formatting.py          — BRL currency formatter
  twilio.py              — TwiML builder
tests/                   — pytest suite (conftest + 6 test modules, 46 test cases)
docker-compose.yml       — Development compose
docker-compose.prod.yml  — Production compose (image from ACR)
db/init.sql              — Postgres schema (bootstrap)
```

---

## 3. Agent Roles

This repository is operated by **three Claude Code agents**. Every agent must
identify its role before acting and respect the boundaries below.

### 🏗️ Development Agent (`/dev`)
- Implements GitHub issues as code changes
- Creates feature branches following naming convention
- Writes or updates tests for every change
- Opens PRs — never pushes directly to `main`
- Follows all code standards in Section 5

### 🔍 QA Agent (`/qa`)
- Reviews every PR before merge (no exceptions)
- Runs the full test suite in the sandbox environment
- Executes end-to-end WhatsApp web tests
- Validates LGPD and security compliance gates
- Approves or blocks PRs with written justification
- Triggers production deploy after successful validation

### 📋 PM Agent (`/pm`)
- Single entry point for new requirements and changes
- Translates requirements into GitHub issues with acceptance criteria
- Prioritises work based on SPEC.md and ICS value
- Monitors Dev and QA agent progress
- Never writes production code directly
- Produces sprint summaries and delivery reports

---

## 4. Mandatory Workflow — Every Change

```
User/PM creates GitHub Issue
         ↓
Dev Agent picks issue → creates branch → implements → opens PR
         ↓
CI Pipeline runs automatically (tests + security scan)
         ↓
QA Agent reviews PR → runs tests → WhatsApp E2E test → compliance check
         ↓
QA approves → PR merged to main
         ↓
CD Pipeline deploys to Azure VM (production)
         ↓
QA validates in production → closes issue
```

### Non-negotiable rules
1. **Every change must have a GitHub issue.** No issue = no branch = no code.
2. **No direct push to `main`.** PRs only, with at least one QA approval.
3. **No merge with failing CI.** All checks must be green.
4. **Conventional commits only.** See Section 5.2.
5. **Secrets never in code.** All secrets via environment variables.
6. **Every new endpoint or service must have tests** before the PR is opened.

---

## 5. Code Standards

### 5.1 Python Style
- **Type hints everywhere.** No untyped functions in `app/`.
- **Pydantic models** for all request/response bodies and config.
- **Parameterised SQL only.** Never f-strings for SQL. Use `%s` or `psycopg2.sql.Identifier`.
- **No bare `except`.** Always catch specific exceptions.
- **Docstrings** on all public functions.
- **Max function length:** 60 lines. Refactor if larger.
- **Linting:** `ruff` (configured in `pyproject.toml` when present).

### 5.2 Conventional Commits
```
<type>(<scope>): <short description>

Types: feat | fix | refactor | test | docs | chore | security | perf
Scope: auth | webhook | onboarding | budgets | chat | db | ci | deps | qa

Examples:
  feat(onboarding): add balance parsing for bare integers
  fix(auth): add JWT exp claim to prevent token immortality
  security(admin): separate ADMIN_SECRET_KEY from SECRET_KEY
  test(webhook): add E2E WhatsApp confirmation flow cases
```

### 5.3 Branch Naming
```
feat/ISSUE-<number>-<slug>
fix/ISSUE-<number>-<slug>
security/ISSUE-<number>-<slug>
test/ISSUE-<number>-<slug>
chore/ISSUE-<number>-<slug>
```

### 5.4 PR Template (always include)
```
## What & Why
Closes #<issue>

## Changes
- ...

## Test coverage
- [ ] Unit tests added/updated
- [ ] Integration tests pass
- [ ] WhatsApp E2E test performed (QA)

## Compliance
- [ ] No PII logged
- [ ] No secrets in code
- [ ] LGPD checklist reviewed
```

---

## 6. Onboarding State Machine

The webhook at `app/main.py` drives users through these states in order:

```
account_snapshot_pending → budget_setup_pending → cost_review_pending
  → card_count_pending → card_names_pending → card_details_pending
  → card_invoice_pending → document_onboarding_pending → onboarding_complete
```

Any change to onboarding logic **requires** a full WhatsApp E2E test through
all states before merge.

---

## 7. Security Requirements (NAVI-SEC)

The following rules are **non-negotiable** and must be verified by QA on every PR:

| ID | Rule | File |
|---|---|---|
| SEC-001 | JWT tokens must have `exp` claim (30 days) | `auth.py` |
| SEC-002 | `ADMIN_SECRET_KEY` must be different from `SECRET_KEY` | `config.py`, `main.py` |
| SEC-003 | All SQL queries use parameterised placeholders | `db.py`, all services |
| SEC-004 | Dynamic table names use `psycopg2.sql.Identifier` | `users.py` |
| SEC-005 | Passwords stored as bcrypt hash, never plaintext | `auth.py` |
| SEC-006 | No secrets or PII in logs or git history | all files |
| SEC-007 | DB connection exceptions always trigger explicit rollback | `db.py` |
| SEC-008 | `get_cursor()` uses connection pool in multi-user scenarios | `db.py` |

---

## 8. LGPD Compliance Checklist

Every PR touching user data must verify:

- [ ] **Minimisation:** Only data necessary for the service is collected
- [ ] **Consent:** User has been informed of data use during onboarding
- [ ] **Retention:** Data is deleted when the user requests via `delete_user_account()`
- [ ] **Portability:** No data is stored outside the identified PostgreSQL tables
- [ ] **No sensitive data in logs:** Phone numbers, balances, and transaction amounts must not appear in application logs
- [ ] **Right to erasure:** `delete_user_account()` covers all tables with `user_id`

---

## 9. ISO 27001 Control Checklist

For changes to infrastructure, auth, or data handling:

- [ ] A.9 — Access control: JWT-protected endpoints, admin key separation
- [ ] A.10 — Cryptography: bcrypt for passwords, HS256 JWT with expiry
- [ ] A.12 — Operations: healthcheck in docker-compose, retry logic in DB init
- [ ] A.13 — Communications: HTTPS enforced in production (Azure), Twilio TLS
- [ ] A.14 — System acquisition: security review in QA gate before every merge
- [ ] A.16 — Incident management: errors return generic messages, no stack traces to client
- [ ] A.18 — Compliance: LGPD checklist completed

---

## 10. Environment Variables Reference

```bash
# Core
APP_NAME, APP_ENV, APP_DEBUG

# JWT
SECRET_KEY          # Signs user JWTs
ADMIN_SECRET_KEY    # Admin endpoint auth — MUST differ from SECRET_KEY
ALGORITHM           # HS256

# External
OPENAI_API_KEY
ACCOUNT_SID         # Twilio
AUTH_TOKEN          # Twilio
TWILIO_NUMBER       # whatsapp:+55...

# Database
DATABASE_HOST, DATABASE_NAME, DATABASE_USER, DATABASE_PASSWORD, DATABASE_PORT

# Azure (CI/CD only)
AZURE_REGISTRY      # navimvpacr461e8d08.azurecr.io
AZURE_VM_RG         # navimvp-hom-rg
AZURE_VM_NAME       # navimvp-hom-vm
```

---

## 11. Running Locally

```bash
# Start all services
docker-compose up --build

# Run tests (requires Postgres on :5432)
SECRET_KEY=test-key pytest tests/ -v --tb=short

# Linting
ruff check app/

# Health check
curl http://localhost:8000/health
```

---

## 12. Production Deploy Path

```
main branch push
  → GitHub Action: build → push image to navimvpacr461e8d08.azurecr.io/navimvp:latest
  → GitHub Action: az vm run-command invoke → docker-compose pull + up -d
  → QA validates /health endpoint on production VM
```

---

*Last updated: 2026-05-03 | Maintained by: PM Agent + QA Agent*
