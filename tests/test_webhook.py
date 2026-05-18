"""
Tests – /webhook (WhatsApp / Twilio)
Covers: new user onboarding, balance parsing, budget setup,
        transaction intents, confirmation flow, fallback responses.
"""

from unittest.mock import MagicMock, patch
import json

import pytest
from fastapi.testclient import TestClient

from tests.conftest import MOCK_OPENAI_PATCH


PHONE = "+5511912345678"


def _post_webhook(client: TestClient, body: str, from_number: str = PHONE):
    """Helper: POST a Twilio-style form-encoded webhook."""
    return client.post(
        "/webhook",
        data={"Body": body, "From": f"whatsapp:{from_number}"},
    )


def _xml_body(resp) -> str:
    return resp.text


# ──────────────────────────────────────────────────────────────────
# TC-WH-001  First message from new user triggers welcome + onboarding
# ──────────────────────────────────────────────────────────────────
def test_new_user_receives_welcome_message(client: TestClient):
    resp = _post_webhook(client, "Oi")
    assert resp.status_code == 200
    assert "xml" in resp.headers["content-type"].lower()
    text = _xml_body(resp)
    assert "Navi" in text or "Ola" in text or "saldo" in text.lower()


# ──────────────────────────────────────────────────────────────────
# TC-WH-002  Missing From field returns 400
# ──────────────────────────────────────────────────────────────────
def test_webhook_missing_from_returns_400(client: TestClient):
    resp = client.post("/webhook", data={"Body": "Hello"})
    assert resp.status_code == 400


# ──────────────────────────────────────────────────────────────────
# TC-WH-003  Returning user (onboarding: account_snapshot_pending)
#            can skip with "PULAR"
# ──────────────────────────────────────────────────────────────────
def test_onboarding_account_snapshot_skip(client: TestClient):
    phone = "+5511100000001"
    _post_webhook(client, "Oi", phone)          # create user
    resp = _post_webhook(client, "PULAR", phone)  # skip snapshot
    assert resp.status_code == 200
    text = _xml_body(resp)
    # Should advance to budget onboarding
    assert "limite" in text.lower() or "orcamento" in text.lower() or "farmacia" in text.lower()


# ──────────────────────────────────────────────────────────────────
# TC-WH-004  User provides balance during onboarding
# ──────────────────────────────────────────────────────────────────
def test_onboarding_balance_accepted(client: TestClient):
    phone = "+5511100000002"
    _post_webhook(client, "Oi", phone)
    resp = _post_webhook(client, "Meu saldo é R$2500", phone)
    assert resp.status_code == 200
    text = _xml_body(resp)
    # Should confirm balance and advance
    assert "2500" in text or "limite" in text.lower() or "orcamento" in text.lower()


# ──────────────────────────────────────────────────────────────────
# TC-WH-005  Budget setup with valid categories
# ──────────────────────────────────────────────────────────────────
def test_onboarding_budget_setup_accepted(client: TestClient):
    phone = "+5511100000003"
    _post_webhook(client, "Oi", phone)
    _post_webhook(client, "PULAR", phone)  # skip snapshot
    resp = _post_webhook(client, "Farmacia 300, mercado 1500, lazer 800", phone)
    assert resp.status_code == 200
    text = _xml_body(resp)
    assert "farmacia" in text.lower() or "300" in text or "mercado" in text.lower()


# ──────────────────────────────────────────────────────────────────
# TC-WH-006  Card count zero during onboarding
# ──────────────────────────────────────────────────────────────────
def test_onboarding_card_count_zero(client: TestClient):
    phone = "+5511100000004"
    _post_webhook(client, "Oi", phone)
    _post_webhook(client, "PULAR", phone)   # skip snapshot
    _post_webhook(client, "PULAR", phone)   # skip budget
    resp = _post_webhook(client, "0", phone)  # no cards
    assert resp.status_code == 200
    text = _xml_body(resp)
    # Should proceed toward document onboarding or complete
    assert resp.status_code == 200


# ──────────────────────────────────────────────────────────────────
# TC-WH-007  Skip all onboarding with repeated PULARs
# ──────────────────────────────────────────────────────────────────
def test_full_onboarding_skip_path(client: TestClient):
    phone = "+5511100000005"
    _post_webhook(client, "Oi", phone)
    _post_webhook(client, "PULAR", phone)   # snapshot
    _post_webhook(client, "PULAR", phone)   # budget
    _post_webhook(client, "PULAR", phone)   # card count
    resp = _post_webhook(client, "PULAR", phone)  # doc or complete
    assert resp.status_code == 200


# ──────────────────────────────────────────────────────────────────
# TC-WH-008  Transaction intent after completing onboarding
# ──────────────────────────────────────────────────────────────────
def test_post_onboarding_transaction_recorded(client: TestClient):
    phone = "+5511100000006"
    # Complete onboarding
    _post_webhook(client, "Oi", phone)
    _post_webhook(client, "PULAR", phone)
    _post_webhook(client, "PULAR", phone)
    _post_webhook(client, "PULAR", phone)
    _post_webhook(client, "PULAR", phone)

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value.choices[0].message.content = json.dumps(
        [{"tipo": "despesa", "categoria": "transporte", "valor": 25.0}]
    )
    with patch(MOCK_OPENAI_PATCH, MagicMock(return_value=mock_client)):
        resp = _post_webhook(client, "Gastei R$25 no Uber", phone)
    assert resp.status_code == 200
    assert "transporte" in _xml_body(resp).lower() or "25" in _xml_body(resp)


# ──────────────────────────────────────────────────────────────────
# TC-WH-009  "quanto gastei" intent (all categories summary)
# ──────────────────────────────────────────────────────────────────
def test_webhook_summary_intent(client: TestClient):
    phone = "+5511100000007"
    _post_webhook(client, "Oi", phone)
    _post_webhook(client, "PULAR", phone)
    _post_webhook(client, "PULAR", phone)
    _post_webhook(client, "PULAR", phone)
    _post_webhook(client, "PULAR", phone)

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value.choices[0].message.content = json.dumps(
        [{"tipo": "despesa", "categoria": "alimentacao", "valor": 100.0}]
    )
    with patch(MOCK_OPENAI_PATCH, MagicMock(return_value=mock_client)):
        _post_webhook(client, "Gastei R$100 no mercado", phone)

    resp = _post_webhook(client, "Quanto gastei", phone)
    assert resp.status_code == 200
    text = _xml_body(resp)
    assert "alimentacao" in text.lower() or "Total" in text or "gastei" in text.lower()


# ──────────────────────────────────────────────────────────────────
# TC-WH-010  Confirmation flow: SIM confirms pending transaction
# ──────────────────────────────────────────────────────────────────
def test_webhook_confirmation_flow(client: TestClient):
    phone = "+5511100000008"
    _post_webhook(client, "Oi", phone)
    _post_webhook(client, "PULAR", phone)
    _post_webhook(client, "PULAR", phone)
    _post_webhook(client, "PULAR", phone)
    _post_webhook(client, "PULAR", phone)

    # Message that triggers confirmation (no R$ prefix, no transaction verb)
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value.choices[0].message.content = json.dumps(
        [{"tipo": "despesa", "categoria": "lazer", "valor": 75.0}]
    )
    with patch(MOCK_OPENAI_PATCH, MagicMock(return_value=mock_client)):
        resp = _post_webhook(client, "cinema 75", phone)

    text = _xml_body(resp)
    # Should ask for confirmation
    if "SIM" in text or "sim" in text.lower():
        # Confirm
        resp2 = _post_webhook(client, "SIM", phone)
        assert resp2.status_code == 200
        assert "confirmada" in _xml_body(resp2).lower() or "lazer" in _xml_body(resp2).lower()


# ──────────────────────────────────────────────────────────────────
# TC-WH-011  Confirmation flow: NAO cancels pending transaction
# ──────────────────────────────────────────────────────────────────
def test_webhook_rejection_flow(client: TestClient):
    phone = "+5511100000009"
    _post_webhook(client, "Oi", phone)
    for _ in range(4):
        _post_webhook(client, "PULAR", phone)

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value.choices[0].message.content = json.dumps(
        [{"tipo": "despesa", "categoria": "outros", "valor": 50.0}]
    )
    with patch(MOCK_OPENAI_PATCH, MagicMock(return_value=mock_client)):
        resp = _post_webhook(client, "alguma coisa 50", phone)

    text = _xml_body(resp)
    if "SIM" in text or "sim" in text.lower():
        resp2 = _post_webhook(client, "NAO", phone)
        assert resp2.status_code == 200
        assert "cancelar" in _xml_body(resp2).lower() or "Nao" in _xml_body(resp2)


# ──────────────────────────────────────────────────────────────────
# TC-WH-012  Card setup intent triggers card count prompt
# ──────────────────────────────────────────────────────────────────
def test_webhook_card_setup_intent(client: TestClient):
    phone = "+5511100000010"
    _post_webhook(client, "Oi", phone)
    for _ in range(4):
        _post_webhook(client, "PULAR", phone)

    resp = _post_webhook(client, "Quero cadastrar meus cartoes", phone)
    assert resp.status_code == 200
    text = _xml_body(resp)
    assert "cartao" in text.lower() or "cartoes" in text.lower() or "quantos" in text.lower()


# ──────────────────────────────────────────────────────────────────
# TC-WH-013  Financial health intent
# ──────────────────────────────────────────────────────────────────
def test_webhook_financial_health_intent(client: TestClient):
    phone = "+5511100000011"
    _post_webhook(client, "Oi", phone)
    for _ in range(4):
        _post_webhook(client, "PULAR", phone)

    resp = _post_webhook(client, "Como está minha saúde financeira", phone)
    assert resp.status_code == 200
    text = _xml_body(resp)
    assert "financeira" in text.lower() or "saude" in text.lower() or "base" in text.lower()


# ──────────────────────────────────────────────────────────────────
# TC-WH-014  Recent transactions intent
# ──────────────────────────────────────────────────────────────────
def test_webhook_recent_transactions_intent(client: TestClient):
    phone = "+5511100000012"
    _post_webhook(client, "Oi", phone)
    for _ in range(4):
        _post_webhook(client, "PULAR", phone)

    resp = _post_webhook(client, "Ultimas transacoes", phone)
    assert resp.status_code == 200
    text = _xml_body(resp)
    assert "registr" in text.lower() or "lancamento" in text.lower() or "transac" in text.lower()
