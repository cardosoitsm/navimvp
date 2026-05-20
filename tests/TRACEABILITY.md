# Navi Requirements Traceability Matrix

> **Every requirement in CLAUDE.md must appear in this file.**
> Every test must be traceable to at least one requirement.
> A requirement with no test is an unverified assumption — a risk.

Last updated: 2026-05-06 | Maintained by: QA Agent

---

## How to read this document

- **REQ-ID**: Internal requirement identifier (format: `AREA-NNN`)
- **Source**: Where the requirement is defined (CLAUDE.md section, SPEC.md)
- **Description**: What must be true
- **Test(s)**: The test function(s) that verify it
- **Status**: `✅ covered` | `⚠️ partial` | `❌ open finding` | `🚧 not yet implemented`

---

## 1. Onboarding State Machine (CLAUDE.md § 6)

| REQ-ID | Description | Test(s) | Status |
|---|---|---|---|
| ONB-001 | New WhatsApp user is auto-registered and receives a welcome message | `TestScenarioA::test_a1_new_user_receives_welcome` | ✅ covered |
| ONB-002 | User can skip every onboarding step with "PULAR" | `TestScenarioA::test_a2_pular_advances_to_budget_setup`, `test_a4_skip_card_setup_completes_onboarding` | ✅ covered |
| ONB-003 | Budget message is parsed and stored during BUDGET_SETUP_PENDING | `TestScenarioA::test_a3_budget_message_accepted`, `test_webhook.py::test_onboarding_budget_setup_accepted` | ✅ covered |
| ONB-004 | ONBOARDING_COMPLETE is the terminal state | `TestScenarioA::test_a4_skip_card_setup_completes_onboarding` | ✅ covered |
| ONB-005 | No onboarding prompts appear after ONBOARDING_COMPLETE | `TestScenarioA::test_a5_post_onboarding_no_further_onboarding_prompts` | ✅ covered |
| ONB-006 | All 9 onboarding states are reachable via natural language | `TestScenarioAFull::test_full_onboarding_with_two_cards` | ✅ covered |
| ONB-007 | Card registration sub-flow (names + details + invoice) works correctly | `TestScenarioAFull::test_full_onboarding_with_two_cards` | ✅ covered |
| ONB-008 | User can register 0 cards (skip card setup entirely) | `test_webhook.py::test_onboarding_card_count_zero` | ✅ covered |

---

## 2. Transaction Recording (CLAUDE.md § 3 / SPEC.md)

| REQ-ID | Description | Test(s) | Status |
|---|---|---|---|
| TXN-001 | Clear transactions (explicit amount + verb) are confirmed immediately | `TestScenarioB::test_b1_clear_transaction_confirmed_immediately` | ✅ covered |
| TXN-002 | Ambiguous transactions trigger a confirmation prompt | `TestScenarioB::test_b2_ambiguous_transaction_triggers_confirmation` | ✅ covered |
| TXN-003 | "SIM" confirms a pending transaction | `TestScenarioB::test_b3_sim_confirms_pending_transaction`, `test_webhook.py::test_webhook_confirmation_flow` | ✅ covered |
| TXN-004 | "NAO" discards a pending transaction | `TestScenarioB::test_b4_nao_discards_pending_transaction`, `test_webhook.py::test_webhook_rejection_flow` | ✅ covered |
| TXN-005 | "Quanto gastei" returns a spending summary | `TestScenarioB::test_b5_spending_summary_after_transactions`, `test_webhook.py::test_webhook_summary_intent` | ✅ covered |
| TXN-006 | Multiple transactions in one message are all recorded | `test_chat.py::test_chat_multiple_transactions_are_all_saved` | ✅ covered |
| TXN-007 | Transactions with valor ≤ 0 are rejected | `test_chat.py::test_chat_zero_value_transaction_is_rejected` | ✅ covered |
| TXN-008 | "Últimas transações" intent returns recent transaction list | `test_webhook.py::test_webhook_recent_transactions_intent` | ✅ covered |

---

## 3. Budget Management (CLAUDE.md § 3 / SPEC.md)

| REQ-ID | Description | Test(s) | Status |
|---|---|---|---|
| BUD-001 | Budget status query returns limit, spent, and remaining | `TestScenarioC::test_c1_budget_status_query_returns_limit_and_remaining` | ✅ covered |
| BUD-002 | Alert fires when spending reaches 80% of limit | `TestScenarioC::test_c2_budget_alert_fires_at_80_percent` | ✅ covered |
| BUD-003 | 80% alert fires at most once per category per month | `TestScenarioC::test_c3_budget_alert_does_not_repeat` | ⚠️ partial — asserts no crash; idempotency not deeply verified |
| BUD-004 | Three-tier alerts: 50%, 80%, 100% | `TestScenarioD::test_d1_all_three_budget_alert_thresholds` | ⚠️ partial — 80%/100% verified; 50% alert text not enforced |
| BUD-005 | Budget feedback included in chat response when budget is configured | `test_chat.py::test_chat_includes_budget_feedback_when_configured` | ✅ covered |

---

## 4. Financial Health (CLAUDE.md § 3 / SPEC.md)

| REQ-ID | Description | Test(s) | Status |
|---|---|---|---|
| FIN-001 | Financial health query returns a classification | `TestScenarioF::test_f1_financial_health_query_returns_classification` | ✅ covered |
| FIN-002 | Health diagnosis reflects real income vs. expense data | `TestScenarioF::test_f2_financial_health_with_income_and_expenses` | ✅ covered |
| FIN-003 | New user with no data receives a graceful "not enough data" response | `TestScenarioF::test_f1_financial_health_query_returns_classification` | ✅ covered |

---

## 5. Conversational UI (CLAUDE.md § 3)

| REQ-ID | Description | Test(s) | Status |
|---|---|---|---|
| UX-001 | No numbered menu options (1., 2., 3.) in any response | `TestScenarioG::test_g1_no_numbered_menus_in_any_response` | ✅ covered |
| UX-002 | Contextual follow-up resolves card from prior context | `TestScenarioG::test_g2_card_invoice_contextual_followup` | ✅ covered |
| UX-003 | Card re-registration intent is handled post-onboarding | `TestScenarioH::test_h1_card_registration_intent_triggers_card_flow`, `test_webhook.py::test_webhook_card_setup_intent` | ✅ covered |
| UX-004 | Financial health intent is handled | `test_webhook.py::test_webhook_financial_health_intent` | ✅ covered |

---

## 6. Security Requirements (CLAUDE.md § 7)

| REQ-ID | Rule | Test(s) | Status |
|---|---|---|---|
| SEC-001 | JWT tokens must include `exp` claim (30 days) | `TestSecurityRequirements::test_sec001_jwt_has_expiry_claim`, `test_auth.py::test_jwt_missing_exp_claim_security_finding` | ❌ open finding — test documents current insecure state |
| SEC-002 | `ADMIN_SECRET_KEY` must differ from `SECRET_KEY` | `TestSecurityRequirements::test_sec002_admin_key_differs_from_secret_key`, `test_auth.py::test_admin_key_equals_secret_key_security_finding` | ❌ open finding — test documents current insecure state |
| SEC-003 | All SQL uses parameterised placeholders | `TestSecurityRequirements::test_sec003_sql_injection_does_not_cause_500`, `test_security.py::test_sql_injection_in_email_field`, `test_security.py::test_sql_injection_in_chat_message` | ✅ covered |
| SEC-004 | Dynamic table names use `psycopg2.sql.Identifier` | Static analysis (QA checklist) — no automated test | ⚠️ partial — QA agent checks code; no automated test |
| SEC-005 | Passwords stored as bcrypt hash | `TestSecurityRequirements::test_sec005_bcrypt_passwords`, `test_security.py::test_password_is_bcrypt_hashed`, `test_auth.py::test_register_...` | ✅ covered |
| SEC-006 | No secrets or PII in logs or git history | Static analysis (Bandit in CI) — no automated test | ⚠️ partial — Bandit scan in CI covers code; git history is manual |
| SEC-007 | DB exceptions trigger explicit rollback | Static analysis (QA checklist) — no automated test | ⚠️ partial — not tested dynamically |
| SEC-008 | get_cursor() uses connection pool in multi-user scenarios | Not yet tested | 🚧 not yet implemented |

---

## 7. LGPD Compliance (CLAUDE.md § 8)

| REQ-ID | Requirement | Test(s) | Status |
|---|---|---|---|
| LGPD-001 | Only necessary data is collected | Code review (QA checklist) | ⚠️ partial — manual review only |
| LGPD-002 | User informed of data use during onboarding | Onboarding message content review | ⚠️ partial — message keywords checked, not full disclosure |
| LGPD-003 | `delete_user_account()` removes data from all tables | `TestLGPDRequirements::test_lgpd_user_deletion_removes_all_tables` | ✅ covered |
| LGPD-004 | No phone numbers or balances in application logs | Static analysis (Bandit, code review) | ⚠️ partial — not asserted dynamically |
| LGPD-005 | `whatsapp:` prefix is stripped before storing phone as PII | `TestLGPDRequirements::test_lgpd_whatsapp_user_not_stored_with_pii_prefix`, `test_security.py::test_whatsapp_user_email_is_phone_number` | ✅ covered |
| LGPD-006 | User data is not stored outside identified PostgreSQL tables | Architecture constraint — verified by code review | ⚠️ partial |

---

## 8. ISO 27001 Controls (CLAUDE.md § 9)

| Control | Description | Test(s) | Status |
|---|---|---|---|
| A.9 — Access control | JWT-protected endpoints, admin key separation | `test_auth.py` (TC-AUTH-008, 009), `test_security.py` (TC-SEC-006) | ✅ covered |
| A.10 — Cryptography | bcrypt passwords, HS256 JWT with expiry | `test_auth.py` (TC-AUTH-007), `test_security.py` (TC-SEC-009) | ⚠️ partial — exp claim missing (SEC-001) |
| A.12 — Operations | Healthcheck in docker-compose, retry logic | `test_health.py`, Docker build check in CI | ✅ covered |
| A.13 — Communications | HTTPS in production, Twilio TLS | Infrastructure (Azure) — not tested in unit scope | ⚠️ partial |
| A.14 — System acquisition | Security review in QA gate | QA agent static analysis checklist | ✅ covered (process) |
| A.16 — Incident management | Errors return generic messages, no stack traces | `test_security.py` (no 500 on injection probes) | ⚠️ partial — not exhaustively checked |
| A.18 — Compliance | LGPD checklist completed | `TestLGPDRequirements`, LGPD rows above | ✅ covered |

---

## 9. API Contract (HTTP endpoints)

| Endpoint | Method | Auth | Test(s) | Status |
|---|---|---|---|---|
| `/health` | GET | None | `test_health.py` | ✅ covered |
| `/register` | POST | None | `test_auth.py` TC-AUTH-001 to 011 | ✅ covered |
| `/login` | POST | None | `test_auth.py` TC-AUTH-003 to 005 | ✅ covered |
| `/chat` | POST | JWT | `test_chat.py` TC-CHAT-001 to 009 | ✅ covered |
| `/webhook` | POST | Twilio signature (optional) | `test_webhook.py` TC-WH-001 to 014 | ✅ covered |
| `/admin/reset-user` | POST | `X-Admin-Key` | `test_auth.py` TC-AUTH-012 to 014, `test_security.py` TC-SEC-006 | ✅ covered |

---

## 10. Open Findings Summary

These are known gaps between requirements and test coverage. Each must be
resolved before the system can be considered fully verified.

| Finding                                        | Severity  | Requirement                                  | Resolution                                                                                   |
| ---------------------------------------------- | --------- | -------------------------------------------- | -------------------------------------------------------------------------------------------- |
| SEC-001: No JWT expiry                         | 🔴 High   | `auth.py::create_token()` must include `exp` | Add `exp = now + timedelta(days=30)` to create_token; update test to assert `exp` in payload |
| SEC-002: ADMIN_SECRET_KEY == SECRET_KEY        | 🔴 High   | Admin key must differ from JWT signing key   | Set ADMIN_SECRET_KEY separately in .env and config.py                                        |
| BUD-003: Alert idempotency not deeply verified | 🟡 Medium | 80% alert fires once per month per category  | Add DB-level test that fires alert twice and checks alert_sent_at timestamp                  |
| SEC-004: Dynamic table names                   | 🟡 Medium | psycopg2.sql.Identifier enforced             | Add static analysis grep test to CI                                                          |
| SEC-007: Rollback on exception                 | 🟡 Medium | get_cursor() rolls back on any exception     | Add test that forces a DB error mid-transaction                                              |
| BUD-004: 50% alert threshold text              | 🟡 Medium | 50% alert exists                             | Verify 50% threshold keyword in alert message                                                |
| LGPD-004: PII in logs                          | 🟡 Medium | No phone numbers in logs                     | Add log capture in test and assert no PII                                                    |

---

*To add a new requirement: add a row to the relevant table, create the test, and update the status.*
*To resolve an open finding: implement the fix, update the test to assert correct behaviour, update status to ✅ covered.*
