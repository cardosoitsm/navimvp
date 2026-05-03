# Navi — Project Context for Claude Code Agents

## 1. What Is Navi
Navi is a WhatsApp-based financial assistant. Users interact exclusively via WhatsApp messages.
Navi records transactions, manages budgets, analyses credit card invoices, and provides financial health assessments.
Target user: Brazilian individual who wants to control personal finances via WhatsApp.

## 2. Tech Stack
- FastAPI (Python 3.12) served on port 8000
- PostgreSQL 15 via psycopg2 (no ORM)
- Twilio for WhatsApp webhook (POST /webhook)
- OpenAI gpt-4.1-mini for transaction classification and document analysis
- JWT auth (python-jose) with bcrypt passwords (passlib)
- Docker Compose for local and production

## 3. Key Files
- app/main.py — all FastAPI routes + WhatsApp webhook orchestration
- app/auth.py — create_token(), verify_token()
- app/config.py — Pydantic settings (get_settings())
- app/db.py — get_connection(), get_cursor(), SCHEMA_STATEMENTS
- app/services/users.py — user CRUD
- app/services/chat.py — OpenAI transaction extraction
- app/services/conversation.py — intent detection, confirmation flow
- app/services/budgets.py — budget parsing, alerts
- app/services/onboarding.py — 9-state onboarding machine
- app/services/financial_health.py — health assessment
- app/services/documents.py — PDF invoice analysis

## 4. Agent Roles
- /pm — PM Agent: creates GitHub issues, coordinates Dev and QA, monitors sprint
- /dev — Dev Agent: implements issues on feature branches, opens PRs
- /qa — QA Agent: reviews PRs, runs tests, E2E WhatsApp testing, merges

## 5. Mandatory Workflow
1. Every change starts with a GitHub issue (PM creates it)
2. Dev works on feature branch: feat/ISSUE-N-slug or fix/ISSUE-N-slug
3. Dev opens PR targeting main, labels status:ready-for-qa
4. QA reviews, runs tests, runs WhatsApp E2E, approves or blocks
5. QA squash-merges approved PRs
6. CD pipeline deploys to Azure VM automatically

No direct push to main. No PR merges without QA approval.

## 6. Onboarding State Machine (9 states)
account_snapshot_pending -> budget_onboarding -> budget_confirmation ->
card_count_pending -> card_names_pending -> card_details_pending ->
invoice_upload_pending -> document_onboarding -> onboarding_complete

ONBOARDING_COMPLETE is the terminal state. All 9 states must remain reachable.

## 7. Security Rules (check on every PR)
- SEC-001: create_token() must include exp claim (JWT expiry)
- SEC-002: admin endpoints use ADMIN_SECRET_KEY, not SECRET_KEY
- SEC-003: all SQL uses %s placeholders — no f-strings or concatenation
- SEC-004: dynamic table names use psycopg2.sql.Identifier
- SEC-005: passwords hashed with bcrypt, never stored plain
- SEC-006: no secrets, tokens, or PII in code or log statements
- SEC-007: get_cursor() includes except: conn.rollback(); raise

## 8. LGPD Compliance
- No PII (phone, balance, transactions) in log statements
- delete_user_account() must cover all tables with user_id columns
- New data collection must be communicated to user in onboarding

## 9. ISO 27001 Controls
- A.9: Depends(get_current_user) on all private endpoints
- A.10: JWT tokens include exp; passwords use bcrypt
- A.16: get_cursor() rolls back on exception

## 10. Environment Variables
SECRET_KEY, ADMIN_SECRET_KEY, ALGORITHM=HS256
DATABASE_HOST, DATABASE_PORT, DATABASE_NAME, DATABASE_USER, DATABASE_PASSWORD
OPENAI_API_KEY, ACCOUNT_SID, AUTH_TOKEN, TWILIO_NUMBER

## 11. Running Locally
docker-compose up --build
# App at http://localhost:8000
# Test: pytest tests/ -v --tb=short

## 12. GitHub Repo
cardosoitsm/navimvp
Branch naming: feat/ISSUE-N-slug, fix/ISSUE-N-slug, security/ISSUE-N-slug
