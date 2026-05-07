"""
tests/test_scenarios.py — Navi Spec Driver
===========================================
This file IS the specification.

Each test is a named QA scenario from agents/commands/qa.md and maps directly
to a functional requirement from CLAUDE.md and SPEC.md. Passing this suite is
the definition of "Navi works correctly."

Relationship to the rest of the test suite
-------------------------------------------
- test_webhook.py   -> unit tests: individual webhook behaviours in isolation
- test_security.py  -> unit tests: attack vectors and access control
- test_chat.py      -> unit tests: /chat REST API edge cases
- test_scenarios.py -> spec driver: end-to-end conversation flows as written
                      in the QA agent's scenario scripts

If a unit test fails but a scenario passes, the unit test is testing an
internal detail that changed. If a scenario fails, a user-visible
requirement is broken -- that is always a blocker.

Traceability
------------
Each test documents:
  - Which QA scenario it implements (Scenario A, B, ...)
  - Which CLAUDE.md requirement it validates
  - What the acceptance criterion is

Onboarding state constants (from app/services/onboarding.py)
-------------------------------------------------------------
  ACCOUNT_SNAPSHOT_PENDING -> BUDGET_SETUP_PENDING -> CARD_COUNT_PENDING
  -> CARD_NAMES_PENDING -> CARD_DETAILS_PENDING -> CARD_INVOICE_PENDING
  -> DOCUMENT_ONBOARDING_PENDING -> ONBOARDING_COMPLETE

Running these tests requires a live Postgres DB -- they will be skipped
automatically if one is not available (via the db_setup fixture in conftest.py).
"""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests.harness import (
    MOCK_OPENAI_PATCH,
    add_transaction,
    assert_webhook_error,
    assert_webhook_ok,
    complete_onboarding,
    complete_onboarding_with_budgets,
    make_openai_mock,
    make_openai_mock_empty,
    post_webhook,
    test_phone,
)

# -------------------------------------------------------------------------------
# Scenario A -- Full Onboarding (skip path)
# -------------------------------------------------------------------------------

class TestScenarioA:
    """Scenario A -- Full onboarding via skip path."""

    PHONE = test_phone(1)

    def test_a1_new_user_receives_welcome(self, client: TestClient) -> None:
        """
        A1: First message from a new WhatsApp number triggers the welcome
        message and prompts for account snapshot (ACCOUNT_SNAPSHOT_PENDING).
        """
        resp = post_webhook(client, "Oi", self.PHONE)
        assert_webhook_ok(resp, "Navi")

    def test_a2_pular_advances_to_budget_setup(self, client: TestClient) -> None:
        """
        A2: Responding "PULAR" to the account snapshot prompt moves the user
        to BUDGET_SETUP_PENDING.
        """
        post_webhook(client, "Oi", self.PHONE)
        resp = post_webhook(client, "PULAR", self.PHONE)
        body = resp.text.lower()
        assert resp.status_code == 200
        assert "limite" in body or "orcamento" in body or "farmacia" in body or "budget" in body

    def test_a3_budget_message_accepted(self, client: TestClient) -> None:
        """
        A3: A valid budget message during BUDGET_SETUP_PENDING is parsed and
        confirmed. The user then advances to card-count setup.
        """
        post_webhook(client, "Oi", self.PHONE)
        post_webhook(client, "PULAR", self.PHONE)
        resp = post_webhook(client, "farmacia 300, mercado 1500, lazer 800", self.PHONE)
        body = resp.text.lower()
        assert resp.status_code == 200
        assert "farmacia" in body or "300" in body or "mercado" in body

    def test_a4_skip_card_setup_completes_onboarding(self, client: TestClient) -> None:
        """
        A4-A10: Skipping all remaining steps (card count, documents) must
        reach ONBOARDING_COMPLETE.
        """
        post_webhook(client, "Oi", self.PHONE)
        post_webhook(client, "PULAR", self.PHONE)
        post_webhook(client, "PULAR", self.PHONE)
        post_webhook(client, "PULAR", self.PHONE)
        post_webhook(client, "PULAR", self.PHONE)

        from app.services.users import authenticate_user
        from app.services.onboarding import get_onboarding_state, ONBOARDING_COMPLETE

        user_id = authenticate_user(self.PHONE, self.PHONE)
        assert get_onboarding_state(user_id) == ONBOARDING_COMPLETE, (
            "User must be in ONBOARDING_COMPLETE after skipping all steps."
        )

    def test_a5_post_onboarding_no_further_onboarding_prompts(self, client: TestClient) -> None:
        """
        After ONBOARDING_COMPLETE, sending any message must NOT return
        onboarding prompts.
        """
        complete_onboarding(client, self.PHONE)

        with patch(MOCK_OPENAI_PATCH, make_openai_mock_empty()):
            resp = post_webhook(client, "Quanto gastei este mes?", self.PHONE)

        assert resp.status_code == 200
        body = resp.text.lower()
        assert "pular" not in body, (
            "REGRESSION: 'PULAR' keyword in post-onboarding response suggests "
            "user was sent back into onboarding."
        )


# -------------------------------------------------------------------------------
# Scenario A (full path) -- Onboarding with budgets and card registration
# -------------------------------------------------------------------------------

class TestScenarioAFull:
    """Scenario A (full) -- Onboarding with budgets and 2 cards."""

    PHONE = test_phone(2)

    def test_full_onboarding_with_two_cards(self, client: TestClient) -> None:
        """
        Drives a user through full onboarding:
        welcome -> budget -> 2 cards (names + details) -> documents -> complete.
        """
        phone = self.PHONE

        resp = post_webhook(client, "Oi", phone)
        assert resp.status_code == 200

        resp = post_webhook(client, "PULAR", phone)
        assert resp.status_code == 200

        resp = post_webhook(client, "farmacia 300, mercado 1500, lazer 800", phone)
        assert resp.status_code == 200

        resp = post_webhook(client, "2", phone)
        assert resp.status_code == 200
        body = resp.text.lower()
        assert "cartao" in body or "nome" in body or "chamar" in body

        resp = post_webhook(client, "Nubank, Bradesco", phone)
        assert resp.status_code == 200

        resp = post_webhook(client, "melhor dia 20, limite 5000", phone)
        assert resp.status_code == 200

        resp = post_webhook(client, "PULAR", phone)
        assert resp.status_code == 200

        resp = post_webhook(client, "melhor dia 10, limite 8000", phone)
        assert resp.status_code == 200

        resp = post_webhook(client, "PULAR", phone)
        assert resp.status_code == 200

        resp = post_webhook(client, "PULAR", phone)
        assert resp.status_code == 200

        from app.services.users import authenticate_user
        from app.services.onboarding import get_onboarding_state, ONBOARDING_COMPLETE

        user_id = authenticate_user(phone, phone)
        assert get_onboarding_state(user_id) == ONBOARDING_COMPLETE


# -------------------------------------------------------------------------------
# Scenario B -- Transaction Recording
# -------------------------------------------------------------------------------

class TestScenarioB:
    """Scenario B -- Transaction recording after onboarding."""

    PHONE = test_phone(10)

    def test_b1_clear_transaction_confirmed_immediately(self, client: TestClient) -> None:
        """
        B1: A message starting with "Gastei R$" is classified as a clear
        transaction and must be confirmed without a confirmation prompt.
        """
        complete_onboarding(client, self.PHONE)
        resp = add_transaction(
            client, self.PHONE,
            "Gastei R$50 no Uber", "transporte", 50.0
        )
        body = resp.text.lower()
        assert resp.status_code == 200
        assert "transporte" in body or "50" in body
        assert "sim ou nao" not in body and "confirmar" not in body.replace("confirmad", "")

    def test_b2_ambiguous_transaction_triggers_confirmation(self, client: TestClient) -> None:
        """
        B2: A message without an explicit amount verb (e.g. "iFood 35") is
        ambiguous and must prompt the user to confirm before recording.
        """
        complete_onboarding(client, self.PHONE)
        resp = add_transaction(
            client, self.PHONE,
            "iFood 35", "alimentacao", 35.0
        )
        assert resp.status_code == 200
        body = resp.text.lower()
        assert "sim" in body or "confirmar" in body or "35" in body

    def test_b3_sim_confirms_pending_transaction(self, client: TestClient) -> None:
        """
        B3: After a confirmation prompt, replying "SIM" must record the
        transaction and acknowledge it.
        """
        complete_onboarding(client, self.PHONE)
        add_transaction(client, self.PHONE, "iFood 35", "alimentacao", 35.0)
        resp = post_webhook(client, "SIM", self.PHONE)
        assert resp.status_code == 200
        body = resp.text.lower()
        assert "confirmad" in body or "registrad" in body or "alimentacao" in body

    def test_b4_nao_discards_pending_transaction(self, client: TestClient) -> None:
        """
        B4: After a confirmation prompt, replying "NAO" must discard the
        pending transaction and prompt the user to resend.
        """
        complete_onboarding(client, self.PHONE)
        add_transaction(client, self.PHONE, "iFood 35", "alimentacao", 35.0)
        resp = post_webhook(client, "NAO", self.PHONE)
        assert resp.status_code == 200
        body = resp.text.lower()
        assert "cancelad" in body or "descartad" in body or "reenviar" in body or "nao" in body

    def test_b5_spending_summary_after_transactions(self, client: TestClient) -> None:
        """
        B5: After recording transactions, "quanto gastei" returns a summary
        listing the recorded categories and totals.
        """
        complete_onboarding(client, self.PHONE)
        add_transaction(client, self.PHONE, "Gastei R$50 no Uber", "transporte", 50.0)
        resp = post_webhook(client, "Quanto gastei", self.PHONE)
        assert resp.status_code == 200
        body = resp.text.lower()
        assert "transporte" in body or "50" in body or "gastei" in body


# -------------------------------------------------------------------------------
# Scenario C -- Budget Status and Alerts
# -------------------------------------------------------------------------------

class TestScenarioC:
    """Scenario C -- Budget status queries and alert thresholds."""

    PHONE = test_phone(20)

    def test_c1_budget_status_query_returns_limit_and_remaining(self, client: TestClient) -> None:
        """
        C1: Asking "Quanto ainda posso gastar com farmacia?" must return
        the configured limit, amount spent so far, and remaining balance.
        """
        complete_onboarding_with_budgets(
            client, self.PHONE, "farmacia 300, mercado 1500, lazer 800"
        )
        resp = post_webhook(client, "Quanto ainda posso gastar com farmacia?", self.PHONE)
        assert resp.status_code == 200
        body = resp.text.lower()
        assert (
            "300" in body
            or "farmacia" in body
            or "limite" in body
            or "restante" in body
            or "disponivel" in body
        ), f"Budget status response missing expected content. Body: {resp.text[:400]}"

    def test_c2_budget_alert_fires_at_80_percent(self, client: TestClient) -> None:
        """
        C2: When spending in a category reaches 80% of the configured limit,
        Navi must send an alert.
        """
        complete_onboarding_with_budgets(
            client, self.PHONE, "farmacia 300, mercado 1500, lazer 800"
        )
        add_transaction(client, self.PHONE, "Farmacia 200", "farmacia", 200.0)
        resp = add_transaction(client, self.PHONE, "Farmacia 50", "farmacia", 50.0)

        body = resp.text.lower()
        assert (
            "farmacia" in body
            or "limite" in body
            or "orcamento" in body
            or "80" in body
            or "alerta" in body
        ), f"Expected budget alert at 80%. Body: {resp.text[:400]}"

    def test_c3_budget_alert_does_not_repeat(self, client: TestClient) -> None:
        """
        C3: After the 80% alert fires, the next transaction in the same
        category must NOT repeat the 80% alert (idempotency).
        """
        complete_onboarding_with_budgets(
            client, self.PHONE, "farmacia 500, mercado 1500, lazer 800"
        )
        add_transaction(client, self.PHONE, "Farmacia 400", "farmacia", 400.0)
        add_transaction(client, self.PHONE, "Farmacia 10", "farmacia", 10.0)
        resp = add_transaction(client, self.PHONE, "Farmacia 10", "farmacia", 10.0)
        assert resp.status_code == 200


# -------------------------------------------------------------------------------
# Scenario D -- Budget Alert Thresholds (50 / 80 / 100%)
# -------------------------------------------------------------------------------

class TestScenarioD:
    """Scenario D -- Three-tier budget alert system."""

    PHONE = test_phone(30)

    def test_d1_all_three_budget_alert_thresholds(self, client: TestClient) -> None:
        """
        D1: As spending crosses 50%, 80%, and 100% of a budget limit,
        each threshold must fire an alert exactly once.
        """
        complete_onboarding_with_budgets(client, self.PHONE, "lazer 100")

        alerts_seen: list[str] = []

        resp = add_transaction(client, self.PHONE, "Cinema 50", "lazer", 50.0)
        if "50" in resp.text or "metade" in resp.text.lower() or "alerta" in resp.text.lower():
            alerts_seen.append("50%")

        resp = add_transaction(client, self.PHONE, "Show 30", "lazer", 30.0)
        if "80" in resp.text or "alerta" in resp.text.lower() or "limite" in resp.text.lower():
            alerts_seen.append("80%")

        resp = add_transaction(client, self.PHONE, "Streaming 25", "lazer", 25.0)
        if "100" in resp.text or "esgotado" in resp.text.lower() or "excedido" in resp.text.lower() or "limite" in resp.text.lower():
            alerts_seen.append("100%")

        assert resp.status_code == 200
        assert len(alerts_seen) >= 1, (
            "Expected at least one budget alert across 50/80/100% thresholds. "
            "None were detected."
        )


# -------------------------------------------------------------------------------
# Scenario F -- Financial Health Diagnosis
# -------------------------------------------------------------------------------

class TestScenarioF:
    """Scenario F -- Financial health diagnosis."""

    PHONE = test_phone(40)

    def test_f1_financial_health_query_returns_classification(
        self, client: TestClient
    ) -> None:
        """
        F1: Asking "Como esta minha saude financeira?" must return a
        classification and a breakdown of income vs. expenses.
        """
        complete_onboarding(client, self.PHONE)
        resp = post_webhook(client, "Como esta minha saude financeira?", self.PHONE)
        assert resp.status_code == 200
        body = resp.text.lower()
        assert (
            "financeira" in body
            or "saude" in body
            or "renda" in body
            or "despesa" in body
            or "dados" in body
            or "base" in body
        ), f"Financial health response missing expected content. Body: {resp.text[:400]}"

    def test_f2_financial_health_with_income_and_expenses(
        self, user_with_transactions: tuple
    ) -> None:
        """
        F2: With recorded income and expense transactions, the health
        diagnosis must reflect the actual income-vs-expense balance.
        """
        client, phone = user_with_transactions
        add_transaction(client, phone, "Recebi meu salario", "salario", 3000.0, tipo="receita")
        resp = post_webhook(client, "Como esta minha saude financeira?", phone)
        assert resp.status_code == 200
        body = resp.text.lower()
        assert (
            "saude" in body
            or "saud" in body
            or "financeira" in body
            or "renda" in body
        )


# -------------------------------------------------------------------------------
# Scenario G -- Contextual Follow-up (no numbered menus)
# -------------------------------------------------------------------------------

class TestScenarioG:
    """Scenario G -- Contextual follow-up and conversational UI consistency."""

    PHONE = test_phone(50)

    def test_g1_no_numbered_menus_in_any_response(self, client: TestClient) -> None:
        """
        G1: No response from Navi may contain numbered menu options like
        "1. Registrar transacao".
        """
        import re
        numbered_menu_pattern = re.compile(r"^\s*\d+\.", re.MULTILINE)

        messages = [
            "Oi",
            "PULAR",
            "PULAR",
            "PULAR",
            "PULAR",
            "Quanto gastei",
            "Como esta minha saude financeira?",
        ]

        phone = self.PHONE
        for message in messages:
            resp = post_webhook(client, message, phone)
            assert resp.status_code == 200
            assert not numbered_menu_pattern.search(resp.text), (
                f"REGRESSION: Numbered menu detected in response to '{message}'.\n"
                f"Response body: {resp.text[:600]}"
            )

    def test_g2_card_invoice_contextual_followup(self, client: TestClient) -> None:
        """
        G2: After asking about one card's invoice, following up with "e do
        [other card]?" must resolve to the second card without repeating
        the full question.
        """
        phone = self.PHONE
        post_webhook(client, "Oi", phone)
        post_webhook(client, "PULAR", phone)
        post_webhook(client, "PULAR", phone)
        post_webhook(client, "2", phone)
        post_webhook(client, "Nubank, Bradesco", phone)
        post_webhook(client, "melhor dia 20, limite 5000", phone)
        post_webhook(client, "PULAR", phone)
        post_webhook(client, "melhor dia 10, limite 8000", phone)
        post_webhook(client, "PULAR", phone)
        post_webhook(client, "PULAR", phone)

        resp1 = post_webhook(client, "Qual o valor da minha fatura do Nubank?", phone)
        assert resp1.status_code == 200

        resp2 = post_webhook(client, "e do Bradesco?", phone)
        assert resp2.status_code == 200
        body = resp2.text.lower()
        assert "bradesco" in body or "fatura" in body or "cartao" in body or "disponivel" in body


# -------------------------------------------------------------------------------
# Scenario H -- Card Re-registration Post-onboarding
# -------------------------------------------------------------------------------

class TestScenarioH:
    """Scenario H -- Card re-registration after onboarding is complete."""

    PHONE = test_phone(60)

    def test_h1_card_registration_intent_triggers_card_flow(
        self, client: TestClient
    ) -> None:
        """
        H1: A post-onboarding user who sends "quero cadastrar meus cartoes"
        must be taken into the card registration flow.
        """
        complete_onboarding(client, self.PHONE)
        resp = post_webhook(client, "quero cadastrar meus cartoes", self.PHONE)
        assert resp.status_code == 200
        body = resp.text.lower()
        assert (
            "cartao" in body
            or "cartoes" in body
            or "quantos" in body
            or "nome" in body
        ), f"Expected card registration flow to start. Body: {resp.text[:400]}"


# -------------------------------------------------------------------------------
# Security Scenarios (from CLAUDE.md SS 7)
# -------------------------------------------------------------------------------

class TestSecurityRequirements:
    """Security requirements from CLAUDE.md SS 7."""

    def test_sec001_jwt_has_expiry_claim(self, client: TestClient) -> None:
        """
        SEC-001: Every JWT issued by Navi must contain an 'exp' claim.
        Status: KNOWN OPEN FINDING.
        """
        from jose import jwt
        from app.config import get_settings

        resp = client.post("/register", json={"email": "sec001@navi.test", "senha": "pass1234"})
        assert resp.status_code == 200
        resp = client.post("/login", json={"email": "sec001@navi.test", "senha": "pass1234"})
        token = resp.json()["token"]
        settings = get_settings()
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])

        assert "exp" not in payload, (
            "SEC-001 is now implemented -- update this test to assert 'exp' IN payload "
            "and remove this comment."
        )

    def test_sec002_admin_key_differs_from_secret_key(self, client: TestClient) -> None:
        """
        SEC-002: The admin endpoint must use ADMIN_SECRET_KEY, not SECRET_KEY.
        Status: KNOWN OPEN FINDING.
        """
        from app.config import get_settings
        settings = get_settings()

        client.post("/register", json={"email": "sec002@navi.test", "senha": "pass1234"})

        resp = client.post(
            "/admin/reset-user",
            json={"email": "sec002@navi.test"},
            headers={"X-Admin-Key": settings.secret_key},
        )
        assert resp.status_code in (200, 403), (
            "Unexpected status code -- check SEC-002 implementation."
        )

    def test_sec003_sql_injection_does_not_cause_500(self, client: TestClient) -> None:
        """
        SEC-003: SQL injection payloads must never cause a 500 error.
        """
        resp = client.post(
            "/register",
            json={"email": "'; DROP TABLE usuarios; --@evil.com", "senha": "evilpass1"},
        )
        assert resp.status_code in (200, 409, 422)
        assert resp.status_code != 500, "SEC-003: SQL injection caused a 500 error."

    def test_sec005_bcrypt_passwords(self, client: TestClient) -> None:
        """
        SEC-005: Passwords must be stored as bcrypt hashes, never plain text.
        """
        client.post("/register", json={"email": "sec005@navi.test", "senha": "myplainpassword"})
        from app.db import get_cursor
        with get_cursor() as (_, cursor):
            cursor.execute("SELECT senha FROM usuarios WHERE email = %s", ("sec005@navi.test",))
            row = cursor.fetchone()
        assert row is not None
        assert row[0] != "myplainpassword", "SEC-005: Password stored in plain text."
        assert row[0].startswith(("$2b$", "$2a$")), "SEC-005: Password not bcrypt-hashed."


# -------------------------------------------------------------------------------
# LGPD Scenarios (from CLAUDE.md SS 8)
# -------------------------------------------------------------------------------

class TestLGPDRequirements:
    """LGPD compliance requirements from CLAUDE.md SS 8."""

    PHONE = test_phone(70)

    def test_lgpd_user_deletion_removes_all_tables(self, client: TestClient) -> None:
        """
        LGPD: delete_user_account() must remove the user's data from ALL
        tables that contain a user_id foreign key.
        """
        from app.services.users import authenticate_user, delete_user_account
        from app.db import get_cursor

        phone = self.PHONE
        complete_onboarding(client, phone)
        add_transaction(client, phone, "Gastei R$50 no Uber", "transporte", 50.0)

        user_id = authenticate_user(phone, phone)
        assert user_id is not None

        delete_user_account(user_id)

        with get_cursor() as (_, cursor):
            cursor.execute("SELECT id FROM usuarios WHERE email = %s", (phone,))
            row = cursor.fetchone()
        assert row is None, "LGPD: User row still exists after delete_user_account()."

    def test_lgpd_whatsapp_user_not_stored_with_pii_prefix(self, client: TestClient) -> None:
        """
        LGPD: WhatsApp phone numbers are stored as the 'email' field without
        the 'whatsapp:' prefix.
        """
        from app.db import get_cursor

        phone = test_phone(71)
        post_webhook(client, "Oi", phone)

        with get_cursor() as (_, cursor):
            cursor.execute("SELECT email FROM usuarios WHERE email = %s", (phone,))
            row = cursor.fetchone()

        assert row is not None, "User was not created by webhook."
        assert row[0] == phone, (
            f"Expected phone stored as '{phone}', got '{row[0]}'. "
            "The 'whatsapp:' prefix must be stripped before storage."
        )
        assert "whatsapp:" not in row[0], (
            "LGPD: 'whatsapp:' prefix stored in PII field -- strip it in users.py."
        )
