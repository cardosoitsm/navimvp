from __future__ import annotations
import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch
from typing import Generator
import pytest
from fastapi.testclient import TestClient

MOCK_OPENAI_PATCH = 'app.services.chat.OpenAI'
MOCK_ONBOARDING_OPENAI_PATCH = 'app.services.onboarding.OpenAI'

def test_phone(index: int) -> str:
    if not 1 <= index <= 9_999:
        raise ValueError(f'test_phone index must be 1-9999, got {index}')
    return f'+55009{index:08d}'

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
    mock = make_openai_mock(tipo=tipo, categoria=categoria, valor=valor)
    with patch(MOCK_OPENAI_PATCH, mock):
        return post_webhook(client, message, phone)

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
    phone = test_phone(902)
    complete_onboarding_with_budgets(client, phone)
    return client, phone

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
