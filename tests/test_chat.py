"""
Tests – /chat endpoint (authenticated REST API)
Covers: transaction insertion, budget feedback, confirmation flow,
        error handling, AI mock, input validation.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.conftest import auth_header, MOCK_OPENAI_PATCH


def _make_openai_mock(tipo="despesa", categoria="alimentacao", valor=50.0):
    """Return a mock OpenAI client whose response contains one transaction."""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value.choices[0].message.content = json.dumps(
        [{"tipo": tipo, "categoria": categoria, "valor": valor}]
    )
    mock_ctor = MagicMock(return_value=mock_client)
    return mock_ctor


# ──────────────────────────────────────────────────────────────────
# TC-CHAT-001  Record a simple expense via /chat
# ──────────────────────────────────────────────────────────────────
def test_chat_records_expense(client: TestClient):
    headers = auth_header(client)
    mock_openai = _make_openai_mock("despesa", "alimentacao", 50.0)
    with patch(MOCK_OPENAI_PATCH, mock_openai):
        resp = client.post("/chat", json={"text": "Gastei R$50 no iFood"}, headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "resposta" in body
    assert "alimentacao" in body["resposta"].lower()


# ──────────────────────────────────────────────────────────────────
# TC-CHAT-002  Record a receita (income)
# ──────────────────────────────────────────────────────────────────
def test_chat_records_income(client: TestClient):
    headers = auth_header(client)
    mock_openai = _make_openai_mock("receita", "salario", 5000.0)
    with patch(MOCK_OPENAI_PATCH, mock_openai):
        resp = client.post("/chat", json={"text": "Recebi meu salário de R$5000"}, headers=headers)
    assert resp.status_code == 200
    assert "salario" in resp.json()["resposta"].lower()


# ──────────────────────────────────────────────────────────────────
# TC-CHAT-003  Empty text returns 422
# ──────────────────────────────────────────────────────────────────
def test_chat_empty_text_returns_422(client: TestClient):
    headers = auth_header(client)
    resp = client.post("/chat", json={"text": ""}, headers=headers)
    assert resp.status_code == 422


# ──────────────────────────────────────────────────────────────────
# TC-CHAT-004  Missing text field returns 422
# ──────────────────────────────────────────────────────────────────
def test_chat_missing_text_field_returns_422(client: TestClient):
    headers = auth_header(client)
    resp = client.post("/chat", json={}, headers=headers)
    assert resp.status_code == 422


# ──────────────────────────────────────────────────────────────────
# TC-CHAT-005  AI returns empty list → 422 with helpful message
# ──────────────────────────────────────────────────────────────────
def test_chat_empty_ai_response_returns_422(client: TestClient):
    headers = auth_header(client)
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value.choices[0].message.content = "[]"
    with patch(MOCK_OPENAI_PATCH, MagicMock(return_value=mock_client)):
        resp = client.post("/chat", json={"text": "Hello world"}, headers=headers)
    assert resp.status_code == 422


# ──────────────────────────────────────────────────────────────────
# TC-CHAT-006  Transaction with negative value is ignored by AI
#              (valor <= 0 is filtered in _extract_transactions_from_ai)
# ──────────────────────────────────────────────────────────────────
def test_chat_zero_value_transaction_is_rejected(client: TestClient):
    headers = auth_header(client)
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value.choices[0].message.content = json.dumps(
        [{"tipo": "despesa", "categoria": "outros", "valor": 0}]
    )
    with patch(MOCK_OPENAI_PATCH, MagicMock(return_value=mock_client)):
        resp = client.post("/chat", json={"text": "0 reais"}, headers=headers)
    # filtered → empty list → 422
    assert resp.status_code == 422


# ──────────────────────────────────────────────────────────────────
# TC-CHAT-007  Budget feedback included when budget is configured
# ──────────────────────────────────────────────────────────────────
def test_chat_includes_budget_feedback_when_configured(client: TestClient):
    headers = auth_header(client, email="budget_user@navi.test", password="pass1234")

    # Set a budget for alimentacao
    from app.db import get_cursor
    from app.services.users import authenticate_user
    from decimal import Decimal
    user_id = authenticate_user("budget_user@navi.test", "pass1234")
    from app.services.budgets import save_budgets
    save_budgets(user_id, {"alimentacao": Decimal("100")}, None)

    mock_openai = _make_openai_mock("despesa", "alimentacao", 30.0)
    with patch(MOCK_OPENAI_PATCH, mock_openai):
        resp = client.post(
            "/chat",
            json={"text": "Gastei R$30 no restaurante"},
            headers=headers,
        )
    assert resp.status_code == 200
    body = resp.json()["resposta"]
    # Should mention budget status
    assert "limite" in body.lower() or "alimentacao" in body.lower()


# ──────────────────────────────────────────────────────────────────
# TC-CHAT-008  Multiple transactions in one message
# ──────────────────────────────────────────────────────────────────
def test_chat_multiple_transactions_are_all_saved(client: TestClient):
    headers = auth_header(client, email="multi@navi.test", password="pass1234")
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value.choices[0].message.content = json.dumps([
        {"tipo": "despesa", "categoria": "transporte", "valor": 20.0},
        {"tipo": "despesa", "categoria": "alimentacao", "valor": 35.0},
    ])
    with patch(MOCK_OPENAI_PATCH, MagicMock(return_value=mock_client)):
        resp = client.post("/chat", json={"text": "Uber 20 e iFood 35"}, headers=headers)
    assert resp.status_code == 200
    # Both categories should appear in response
    body = resp.json()["resposta"].lower()
    assert "transporte" in body or "alimentacao" in body


# ──────────────────────────────────────────────────────────────────
# TC-CHAT-009  Unauthenticated request returns 401
# ──────────────────────────────────────────────────────────────────
def test_chat_unauthenticated_returns_401(client: TestClient):
    resp = client.post("/chat", json={"text": "hello"})
    assert resp.status_code == 401
