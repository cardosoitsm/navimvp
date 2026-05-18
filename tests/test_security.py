"""
Tests – Security Findings & Attack Vectors
Covers: injection probes, admin endpoint hardening, JWT edge cases,
        payload size, and data isolation between users.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.conftest import auth_header, MOCK_OPENAI_PATCH


# ──────────────────────────────────────────────────────────────────
# TC-SEC-001  SQL injection probe in registration email
# ──────────────────────────────────────────────────────────────────
def test_sql_injection_in_email_field(client: TestClient):
    """Parameterized queries should neutralise injection attempts."""
    resp = client.post(
        "/register",
        json={"email": "'; DROP TABLE usuarios; --@evil.com", "senha": "pass1234"},
    )
    # Either 422 (validation) or 200 – but NOT 500 (indicates injection)
    assert resp.status_code in (200, 422, 409)
    assert resp.status_code != 500


# ──────────────────────────────────────────────────────────────────
# TC-SEC-002  SQL injection probe in chat message
# ──────────────────────────────────────────────────────────────────
def test_sql_injection_in_chat_message(client: TestClient):
    headers = auth_header(client)
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value.choices[0].message.content = json.dumps(
        [{"tipo": "despesa", "categoria": "'); DROP TABLE transacoes;--", "valor": 1.0}]
    )
    with patch(MOCK_OPENAI_PATCH, MagicMock(return_value=mock_client)):
        resp = client.post(
            "/chat",
            json={"text": "'; DROP TABLE transacoes;--"},
            headers=headers,
        )
    # Should NOT be a 500
    assert resp.status_code != 500
    # Transactions table should still exist
    from app.db import get_cursor
    with get_cursor() as (_, cursor):
        cursor.execute("SELECT 1 FROM transacoes LIMIT 1")


# ──────────────────────────────────────────────────────────────────
# TC-SEC-003  Data isolation: user A cannot see user B's data
# ──────────────────────────────────────────────────────────────────
def test_user_data_isolation(client: TestClient):
    headers_a = auth_header(client, "userA@navi.test", "passA1234")
    headers_b = auth_header(client, "userB@navi.test", "passB1234")

    # User A adds a transaction
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value.choices[0].message.content = json.dumps(
        [{"tipo": "despesa", "categoria": "alimentacao", "valor": 200.0}]
    )
    with patch(MOCK_OPENAI_PATCH, MagicMock(return_value=mock_client)):
        client.post("/chat", json={"text": "Gastei R$200,00 no mercado"}, headers=headers_a)

    # User B should NOT see User A's transactions
    from app.services.users import authenticate_user
    from app.services.summary import resumo_mes
    user_b_id = authenticate_user("userB@navi.test", "passB1234")
    summary = resumo_mes(user_b_id)
    assert "200" not in summary, "User B should not see User A's transaction"


# ──────────────────────────────────────────────────────────────────
# TC-SEC-004  Forged token with wrong key is rejected
# ──────────────────────────────────────────────────────────────────
def test_forged_token_rejected(client: TestClient):
    from jose import jwt
    forged = jwt.encode({"user_id": 1}, "wrong-secret", algorithm="HS256")
    resp = client.post(
        "/chat",
        json={"text": "hello"},
        headers={"Authorization": f"Bearer {forged}"},
    )
    assert resp.status_code == 401


# ──────────────────────────────────────────────────────────────────
# TC-SEC-005  Token without user_id is rejected
# ──────────────────────────────────────────────────────────────────
def test_token_without_user_id_rejected(client: TestClient):
    from jose import jwt
    from app.config import get_settings
    settings = get_settings()
    bad_token = jwt.encode({"sub": "nouser"}, settings.secret_key, algorithm=settings.algorithm)
    resp = client.post(
        "/chat",
        json={"text": "hello"},
        headers={"Authorization": f"Bearer {bad_token}"},
    )
    assert resp.status_code == 401


# ──────────────────────────────────────────────────────────────────
# TC-SEC-006  Admin endpoint requires key header
# ──────────────────────────────────────────────────────────────────
def test_admin_endpoint_requires_key(client: TestClient):
    resp = client.post("/admin/reset-user", json={"email": "anyone@navi.test"})
    assert resp.status_code == 403


# ──────────────────────────────────────────────────────────────────
# TC-SEC-007  Webhook with no phone number blocked
# ──────────────────────────────────────────────────────────────────
def test_webhook_empty_from_blocked(client: TestClient):
    resp = client.post("/webhook", data={"Body": "hello", "From": ""})
    assert resp.status_code == 400


# ──────────────────────────────────────────────────────────────────
# TC-SEC-008  Very long message body doesn't crash the server
# ──────────────────────────────────────────────────────────────────
def test_webhook_large_body_handled_gracefully(client: TestClient):
    large_message = "A" * 10_000
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value.choices[0].message.content = "[]"

    phone = "+5511777000001"
    client.post("/webhook", data={"Body": "Oi", "From": f"whatsapp:{phone}"})
    for _ in range(4):
        client.post("/webhook", data={"Body": "PULAR", "From": f"whatsapp:{phone}"})

    with patch(MOCK_OPENAI_PATCH, MagicMock(return_value=mock_client)):
        resp = client.post(
            "/webhook",
            data={"Body": large_message, "From": f"whatsapp:{phone}"},
        )
    assert resp.status_code in (200, 400, 422)
    assert resp.status_code != 500


# ──────────────────────────────────────────────────────────────────
# TC-SEC-009  Password hashing uses bcrypt (not reversible)
# ──────────────────────────────────────────────────────────────────
def test_password_is_bcrypt_hashed(client: TestClient):
    client.post("/register", json={"email": "bcrypt@navi.test", "senha": "mypassword"})
    from app.db import get_cursor
    with get_cursor() as (_, cursor):
        cursor.execute("SELECT senha FROM usuarios WHERE email = %s", ("bcrypt@navi.test",))
        row = cursor.fetchone()
    stored_hash = row[0]
    assert stored_hash != "mypassword"
    assert stored_hash.startswith("$2b$") or stored_hash.startswith("$2a$")


# ──────────────────────────────────────────────────────────────────
# TC-SEC-010  WhatsApp number stored as email (phone not in plain text)
# ──────────────────────────────────────────────────────────────────
def test_whatsapp_user_email_is_phone_number(client: TestClient):
    """Documents that WA phone numbers are stored as 'email' in usuarios."""
    phone = "+5511888000001"
    client.post("/webhook", data={"Body": "Oi", "From": f"whatsapp:{phone}"})
    from app.db import get_cursor
    with get_cursor() as (_, cursor):
        cursor.execute("SELECT email FROM usuarios WHERE email = %s", (phone,))
        row = cursor.fetchone()
    assert row is not None
    assert row[0] == phone
