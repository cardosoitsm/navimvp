<<<<<<< HEAD
from __future__ import annotations
=======
"""
tests/harness.py — Navi Test Harness
=====================================
Centralised helpers, factories, and fixtures for the Navi test suite.

Why this file exists
--------------------
- Eliminates the repeated 4-5 _post_webhook("PULAR") block that appears in
  every test that needs a post-onboarding user.
- Provides a single source of truth for test phone numbers (LGPD: fake numbers
  that can never be confused with real users).
- Consolidates the OpenAI mock factory that was duplicated across test files.
- Exposes reusable pytest fixtures so new tests can be written in ~10 lines
  instead of 30+.

Usage
-----
    from tests.harness import (
        complete_onboarding,
        add_transaction,
        post_webhook,
        make_openai_mock,
        test_phone,
    )

    # Or use fixtures directly in test functions:
    def test_something(onboarded_user):
        client, phone = onboarded_user
        ...

Fixtures available
------------------
    onboarded_user       → (client, phone)  fresh user past onboarding
    user_with_budget     → (client, phone)  onboarded + farmacia/mercado/lazer budgets
    user_with_transactions → (client, phone) onboarded + 2 recorded transactions
"""

from __future__ import annotations

>>>>>>> bfa62b0 (Add Obsidian workspace and AI architecture files)
import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch
from typing import Generator
<<<<<<< HEAD
import pytest
from fastapi.testclient import TestClient

MOCK_OPENAI_PATCH = 'app.services.chat.OpenAI'
MOCK_ONBOARDING_OPENAI_PATCH = 'app.services.onboarding.OpenAI'

def test_phone(index: int) -> str:
    if not 1 <= index <= 9_999:
        raise ValueError(f'test_phone index must be 1-9999, got {index}')
    return f'+55009{index:08d}'

test_phone.__test__ = False
def post_webhook(client: TestClient, body: str, phone: str) -> object:
    return client.post('/webhook', data={'Body': body, 'From': f'whatsapp:{phone}'})

def make_openai_mock(tipo='despesa', categoria='alimentacao', valor=50.0) -> MagicMock:
    if valor <= 0:
        raise ValueError(f'valor must be positive, got {valor}')
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value.choices[0].message.content = json.dumps(
        [{'tipo': tipo, 'categoria': categoria, 'valor': valor}]
    )
    return MagicMock(return_value=mock_client)

def make_openai_mock_multi(transactions: list) -> MagicMock:
    for t in transactions:
        if t.get('valor', 0) <= 0:
            raise ValueError(f'All transaction values must be > 0. Got: {t}')
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value.choices[0].message.content = json.dumps(transactions)
    return MagicMock(return_value=mock_client)

def make_openai_mock_empty() -> MagicMock:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value.choices[0].message.content = '[]'
    return MagicMock(return_value=mock_client)

def complete_onboarding(client: TestClient, phone: str) -> None:
    post_webhook(client, 'Oi', phone)
    post_webhook(client, 'PULAR', phone)
    post_webhook(client, 'PULAR', phone)
    post_webhook(client, 'PULAR', phone)
    post_webhook(client, 'PULAR', phone)

def complete_onboarding_with_budgets(client, phone, budget_message='farmacia 300, mercado 1500, lazer 800'):
    post_webhook(client, 'Oi', phone)
    post_webhook(client, 'PULAR', phone)
    post_webhook(client, budget_message, phone)
    post_webhook(client, 'PULAR', phone)
    post_webhook(client, 'PULAR', phone)

def add_transaction(client, phone, message, categoria, valor, tipo='despesa'):
=======

import pytest
from fastapi.testclient import TestClient

# ──────────────────────────────────────────────────────────────────────────────
# Patch paths (single source of truth — changes in app structure only need to
# be updated here)
# ──────────────────────────────────────────────────────────────────────────────

MOCK_OPENAI_PATCH = "app.services.chat.OpenAI"
MOCK_ONBOARDING_OPENAI_PATCH = "app.services.onboarding.OpenAI"

# ──────────────────────────────────────────────────────────────────────────────
# Phone number pool
#
# Brazilian mobile numbers follow: +55 <2-digit DDD> <9-digit number starting
# with 9>. Area code "00" does not exist in Brazil, so these numbers are
# provably not real — safe for tests and compliant with LGPD's principle of
# data minimisation (no PII generated during testing).
#
# Rule: each test file that needs unique users should allocate a range:
#   test_webhook.py  → range 1-50    (test_phone(1) … test_phone(50))
#   test_security.py → range 51-100
#   test_chat.py     → range 101-150  (uses email-based users, rarely phones)
#   Future files     → range 151+
# ──────────────────────────────────────────────────────────────────────────────

def test_phone(index: int) -> str:
    """
    Return a deterministic, clearly-fake Brazilian phone number.

    The area code 00 does not exist — these numbers cannot belong to
    real users and are safe to store in test databases.

    Args:
        index: 1-based integer, determines the number (1-9999 supported).

    Returns:
        str: e.g. test_phone(1) → "+5500900000001"
    """
    if not 1 <= index <= 9_999:
        raise ValueError(f"test_phone index must be 1-9999, got {index}")
    return f"+55009{index:08d}"


# ──────────────────────────────────────────────────────────────────────────────
# Low-level webhook helper
# ──────────────────────────────────────────────────────────────────────────────

def post_webhook(client: TestClient, body: str, phone: str) -> object:
    """
    POST a Twilio-style form-encoded message to /webhook.

    Args:
        client:  FastAPI TestClient instance.
        body:    The WhatsApp message text.
        phone:   Sender phone number (without "whatsapp:" prefix).

    Returns:
        The HTTP response object.
    """
    return client.post(
        "/webhook",
        data={"Body": body, "From": f"whatsapp:{phone}"},
    )


# ──────────────────────────────────────────────────────────────────────────────
# OpenAI mock factory
# ──────────────────────────────────────────────────────────────────────────────

def make_openai_mock(
    tipo: str = "despesa",
    categoria: str = "alimentacao",
    valor: float = 50.0,
) -> MagicMock:
    """
    Return a mock OpenAI constructor whose .chat.completions.create()
    returns a response classifying a single transaction.

    Pass the return value directly to unittest.mock.patch:

        with patch(MOCK_OPENAI_PATCH, make_openai_mock("despesa", "transporte", 25.0)):
            resp = post_webhook(client, "Gastei R$25 no Uber", phone)

    Args:
        tipo:      "despesa" or "receita"
        categoria: transaction category string
        valor:     transaction amount (must be > 0)

    Returns:
        MagicMock that wraps the OpenAI constructor.
    """
    if valor <= 0:
        raise ValueError(f"valor must be positive for a valid transaction mock, got {valor}")

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value.choices[0].message.content = json.dumps(
        [{"tipo": tipo, "categoria": categoria, "valor": valor}]
    )
    return MagicMock(return_value=mock_client)


def make_openai_mock_multi(transactions: list[dict]) -> MagicMock:
    """
    Return a mock OpenAI constructor that returns multiple transactions.

    Args:
        transactions: list of dicts, each with keys: tipo, categoria, valor.

    Example:
        make_openai_mock_multi([
            {"tipo": "despesa", "categoria": "transporte", "valor": 20.0},
            {"tipo": "despesa", "categoria": "alimentacao", "valor": 35.0},
        ])
    """
    for t in transactions:
        if t.get("valor", 0) <= 0:
            raise ValueError(f"All transaction values must be > 0. Got: {t}")

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value.choices[0].message.content = json.dumps(
        transactions
    )
    return MagicMock(return_value=mock_client)


def make_openai_mock_empty() -> MagicMock:
    """
    Return a mock OpenAI constructor that returns an empty transaction list.
    Useful for testing fallback / unrecognised message handling.
    """
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value.choices[0].message.content = "[]"
    return MagicMock(return_value=mock_client)


# ──────────────────────────────────────────────────────────────────────────────
# Conversation scenario drivers
#
# These functions reproduce the exact message sequences defined in the QA
# agent's E2E scenarios (agents/commands/qa.md) so the automated test suite
# and the manual QA checklist share the same vocabulary.
# ──────────────────────────────────────────────────────────────────────────────

def complete_onboarding(client: TestClient, phone: str) -> None:
    """
    Drive a new user through the full skip-path onboarding (all PULAR).

    Equivalent to QA Scenario A steps A1, A2 with PULAR for snapshot,
    then PULAR through budget → card count → documents → ONBOARDING_COMPLETE.

    After this call, the user is in state ONBOARDING_COMPLETE and ready
    to receive transaction messages.

    Args:
        client: FastAPI TestClient.
        phone:  Phone number for the test user (use test_phone(N)).
    """
    post_webhook(client, "Oi", phone)       # triggers welcome + ACCOUNT_SNAPSHOT_PENDING
    post_webhook(client, "PULAR", phone)    # skip account snapshot → BUDGET_SETUP_PENDING
    post_webhook(client, "PULAR", phone)    # skip budget setup → CARD_COUNT_PENDING
    post_webhook(client, "PULAR", phone)    # skip card setup → DOCUMENT_PENDING or COMPLETE
    post_webhook(client, "PULAR", phone)    # skip documents → ONBOARDING_COMPLETE


def complete_onboarding_with_budgets(
    client: TestClient,
    phone: str,
    budget_message: str = "farmacia 300, mercado 1500, lazer 800",
) -> None:
    """
    Drive a new user through onboarding with budgets configured but no cards.

    After this call, the user has active budget limits set and is in state
    ONBOARDING_COMPLETE.

    Args:
        client:         FastAPI TestClient.
        phone:          Phone number for the test user.
        budget_message: Natural-language budget string, e.g. "farmacia 300, mercado 1500".
    """
    post_webhook(client, "Oi", phone)
    post_webhook(client, "PULAR", phone)               # skip account snapshot
    post_webhook(client, budget_message, phone)         # set budgets
    post_webhook(client, "PULAR", phone)               # skip card setup
    post_webhook(client, "PULAR", phone)               # skip documents → ONBOARDING_COMPLETE


def add_transaction(
    client: TestClient,
    phone: str,
    message: str,
    categoria: str,
    valor: float,
    tipo: str = "despesa",
) -> object:
    """
    Record a transaction via /webhook with a mocked OpenAI response.

    The OpenAI call is intercepted so the test does not require a real API key
    and the category/value are deterministic.

    Args:
        client:    FastAPI TestClient.
        phone:     Phone number of the user.
        message:   The natural-language message to send (e.g. "Gastei R$50 no Uber").
        categoria: Category the AI should return (e.g. "transporte").
        valor:     Amount the AI should return.
        tipo:      "despesa" (default) or "receita".

    Returns:
        The HTTP response from /webhook.
    """
>>>>>>> bfa62b0 (Add Obsidian workspace and AI architecture files)
    mock = make_openai_mock(tipo=tipo, categoria=categoria, valor=valor)
    with patch(MOCK_OPENAI_PATCH, mock):
        return post_webhook(client, message, phone)

<<<<<<< HEAD
@contextmanager
def patched_openai(tipo='despesa', categoria='alimentacao', valor=50.0):
    with patch(MOCK_OPENAI_PATCH, make_openai_mock(tipo=tipo, categoria=categoria, valor=valor)):
        yield

@pytest.fixture
def onboarded_user(client: TestClient):
    phone = test_phone(901)
    complete_onboarding(client, phone)
    return client, phone

@pytest.fixture
def user_with_budget(client: TestClient):
=======

@contextmanager
def patched_openai(
    tipo: str = "despesa",
    categoria: str = "alimentacao",
    valor: float = 50.0,
) -> Generator[None, None, None]:
    """
    Context manager that patches the chat OpenAI client for the duration of
    a with-block. Convenience wrapper around make_openai_mock.

    Example:
        with patched_openai("despesa", "transporte", 25.0):
            resp = post_webhook(client, "Gastei R$25 no Uber", phone)
    """
    with patch(MOCK_OPENAI_PATCH, make_openai_mock(tipo=tipo, categoria=categoria, valor=valor)):
        yield


# ──────────────────────────────────────────────────────────────────────────────
# pytest fixtures
#
# Import these in test files via:
#   from tests.harness import onboarded_user, user_with_budget, user_with_transactions
#
# Or register them globally in conftest.py by adding:
#   from tests.harness import onboarded_user, user_with_budget, user_with_transactions
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def onboarded_user(client: TestClient):
    """
    Pytest fixture: returns (client, phone) for a user who has completed
    the full skip-path onboarding (no budgets, no cards).

    The underlying `client` fixture resets the DB before each test, so this
    user is always in a clean state.

    Usage:
        def test_something(onboarded_user):
            client, phone = onboarded_user
            resp = post_webhook(client, "Quanto gastei", phone)
    """
    phone = test_phone(901)  # reserved range: fixture-allocated users
    complete_onboarding(client, phone)
    return client, phone


@pytest.fixture
def user_with_budget(client: TestClient):
    """
    Pytest fixture: returns (client, phone) for a user who completed
    onboarding with farmacia/mercado/lazer budgets configured.

    Useful for testing budget alerts, spending summaries, and budget-status
    queries without manual setup.

    Usage:
        def test_budget_alert(user_with_budget):
            client, phone = user_with_budget
            add_transaction(client, phone, "Farmacia 280", "farmacia", 280.0)
    """
>>>>>>> bfa62b0 (Add Obsidian workspace and AI architecture files)
    phone = test_phone(902)
    complete_onboarding_with_budgets(client, phone)
    return client, phone

<<<<<<< HEAD
@pytest.fixture
def user_with_transactions(client: TestClient):
    phone = test_phone(903)
    complete_onboarding(client, phone)
    add_transaction(client, phone, 'Gastei R$50 no Uber', 'transporte', 50.0)
    add_transaction(client, phone, 'Mercado 120', 'alimentacao', 120.0)
    return client, phone

def assert_webhook_ok(resp, *expected_fragments: str) -> None:
    assert resp.status_code == 200, f'Expected 200, got {resp.status_code}. Body: {resp.text[:500]}'
    assert 'xml' in resp.headers.get('content-type', '').lower()
    body_lower = resp.text.lower()
    for fragment in expected_fragments:
        assert fragment.lower() in body_lower, f'Expected fragment not found: {fragment}\nBody: {resp.text[:1000]}'

def assert_webhook_error(resp, expected_status: int) -> None:
    assert resp.status_code == expected_status, f'Expected {expected_status}, got {resp.status_code}. Body: {resp.text[:500]}'
=======

@pytest.fixture
def user_with_transactions(client: TestClient):
    """
    Pytest fixture: returns (client, phone) for a user who has completed
    onboarding and has two recorded transactions:
      - R$50 transporte (Uber)
      - R$120 alimentacao (mercado)

    Useful for testing summaries, financial health, and contextual follow-up
    queries that require prior transaction history.

    Usage:
        def test_summary(user_with_transactions):
            client, phone = user_with_transactions
            resp = post_webhook(client, "Quanto gastei", phone)
            assert "transporte" in resp.text or "alimentacao" in resp.text
    """
    phone = test_phone(903)
    complete_onboarding(client, phone)
    add_transaction(client, phone, "Gastei R$50 no Uber", "transporte", 50.0)
    add_transaction(client, phone, "Mercado 120", "alimentacao", 120.0)
    return client, phone


# ──────────────────────────────────────────────────────────────────────────────
# Assertion helpers
#
# Wrap common response checks so test failures print useful diagnostic info.
# ──────────────────────────────────────────────────────────────────────────────

def assert_webhook_ok(resp, *expected_fragments: str) -> None:
    """
    Assert a /webhook response is 200 and contains all expected text fragments
    (case-insensitive search in the XML body).

    Args:
        resp:               The response object from post_webhook().
        expected_fragments: One or more substrings that must appear in the body.

    Raises:
        AssertionError with a clear diagnostic message on failure.
    """
    assert resp.status_code == 200, (
        f"Expected 200, got {resp.status_code}. Body: {resp.text[:500]}"
    )
    assert "xml" in resp.headers.get("content-type", "").lower(), (
        f"Expected XML response, got content-type: {resp.headers.get('content-type')}"
    )
    body_lower = resp.text.lower()
    for fragment in expected_fragments:
        assert fragment.lower() in body_lower, (
            f"Expected '{fragment}' in response body.\n"
            f"Full body: {resp.text[:1000]}"
        )


def assert_webhook_error(resp, expected_status: int) -> None:
    """
    Assert a /webhook response returns an expected error status code.

    Args:
        resp:            The response object from post_webhook().
        expected_status: Expected HTTP status code (e.g. 400, 422).
    """
    assert resp.status_code == expected_status, (
        f"Expected {expected_status}, got {resp.status_code}. Body: {resp.text[:500]}"
    )
>>>>>>> bfa62b0 (Add Obsidian workspace and AI architecture files)
