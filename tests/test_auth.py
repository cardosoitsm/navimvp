"""
Tests – Authentication & User Management
Covers: register, login, JWT creation, JWT validation, edge cases, security.
"""

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from tests.conftest import auth_header


# ──────────────────────────────────────────────────────────────────
# TC-AUTH-001  New user registration succeeds
# ──────────────────────────────────────────────────────────────────
def test_register_new_user_returns_user_id(client: TestClient):
    resp = client.post("/register", json={"email": "alice@navi.test", "senha": "abc123"})
    assert resp.status_code == 200
    body = resp.json()
    assert "user_id" in body
    assert isinstance(body["user_id"], int)
    assert body["user_id"] > 0


# ──────────────────────────────────────────────────────────────────
# TC-AUTH-002  Duplicate email returns 409
# ──────────────────────────────────────────────────────────────────
def test_register_duplicate_email_returns_409(client: TestClient):
    payload = {"email": "dup@navi.test", "senha": "abc123"}
    client.post("/register", json=payload)
    resp = client.post("/register", json=payload)
    assert resp.status_code == 409


# ──────────────────────────────────────────────────────────────────
# TC-AUTH-003  Login with correct credentials returns token
# ──────────────────────────────────────────────────────────────────
def test_login_valid_credentials_returns_token(client: TestClient):
    client.post("/register", json={"email": "bob@navi.test", "senha": "mypassword"})
    resp = client.post("/login", json={"email": "bob@navi.test", "senha": "mypassword"})
    assert resp.status_code == 200
    body = resp.json()
    assert "token" in body
    assert len(body["token"]) > 20


# ──────────────────────────────────────────────────────────────────
# TC-AUTH-004  Login with wrong password returns 401
# ──────────────────────────────────────────────────────────────────
def test_login_wrong_password_returns_401(client: TestClient):
    client.post("/register", json={"email": "carol@navi.test", "senha": "correct"})
    resp = client.post("/login", json={"email": "carol@navi.test", "senha": "wrong"})
    assert resp.status_code == 401


# ──────────────────────────────────────────────────────────────────
# TC-AUTH-005  Login with non-existent user returns 401
# ──────────────────────────────────────────────────────────────────
def test_login_nonexistent_user_returns_401(client: TestClient):
    resp = client.post("/login", json={"email": "ghost@navi.test", "senha": "doesntmatter"})
    assert resp.status_code == 401


# ──────────────────────────────────────────────────────────────────
# TC-AUTH-006  JWT payload contains user_id
# ──────────────────────────────────────────────────────────────────
def test_jwt_payload_contains_user_id(client: TestClient):
    client.post("/register", json={"email": "dave@navi.test", "senha": "pass1234"})
    resp = client.post("/login", json={"email": "dave@navi.test", "senha": "pass1234"})
    token = resp.json()["token"]
    from app.config import get_settings
    settings = get_settings()
    payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    assert "user_id" in payload
    assert isinstance(payload["user_id"], int)


# ──────────────────────────────────────────────────────────────────
# TC-AUTH-007  [SECURITY] JWT has no expiry – flagged as finding
# ──────────────────────────────────────────────────────────────────
def test_jwt_missing_exp_claim_security_finding(client: TestClient):
    """
    SECURITY FINDING: JWT tokens issued by Navi have no 'exp' claim.
    Tokens are valid forever until the SECRET_KEY is rotated.
    This test documents the current behaviour so the team is aware.
    """
    client.post("/register", json={"email": "exp@navi.test", "senha": "pass1234"})
    resp = client.post("/login", json={"email": "exp@navi.test", "senha": "pass1234"})
    token = resp.json()["token"]
    from app.config import get_settings
    settings = get_settings()
    payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    assert "exp" not in payload, (
        "SECURITY: Token has no expiry. "
        "Recommendation: add exp = datetime.utcnow() + timedelta(days=30) to create_token()."
    )


# ──────────────────────────────────────────────────────────────────
# TC-AUTH-008  Protected route rejects missing token
# ──────────────────────────────────────────────────────────────────
def test_protected_chat_rejects_missing_token(client: TestClient):
    resp = client.post("/chat", json={"text": "hello"})
    assert resp.status_code == 401


# ──────────────────────────────────────────────────────────────────
# TC-AUTH-009  Protected route rejects malformed token
# ──────────────────────────────────────────────────────────────────
def test_protected_chat_rejects_invalid_token(client: TestClient):
    resp = client.post(
        "/chat",
        json={"text": "hello"},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code == 401


# ──────────────────────────────────────────────────────────────────
# TC-AUTH-010  Registration rejects too-short password
# ──────────────────────────────────────────────────────────────────
def test_register_short_password_returns_422(client: TestClient):
    resp = client.post("/register", json={"email": "short@navi.test", "senha": "abc"})
    assert resp.status_code == 422


# ──────────────────────────────────────────────────────────────────
# TC-AUTH-011  Registration rejects invalid email format
# ──────────────────────────────────────────────────────────────────
def test_register_invalid_email_returns_422(client: TestClient):
    resp = client.post("/register", json={"email": "not-an-email", "senha": "abc123"})
    assert resp.status_code == 422


# ──────────────────────────────────────────────────────────────────
# TC-AUTH-012  [SECURITY] Admin key == SECRET_KEY – flagged finding
# ──────────────────────────────────────────────────────────────────
def test_admin_key_equals_secret_key_security_finding(client: TestClient):
    """
    SECURITY FINDING: /admin/reset-user uses the same SECRET_KEY that
    signs JWT tokens as its admin authentication key. These should be
    different secrets.
    """
    from app.config import get_settings
    settings = get_settings()

    # Register a user we will try to delete
    client.post("/register", json={"email": "victim@navi.test", "senha": "pass1234"})

    # Using SECRET_KEY as X-Admin-Key works – this documents the risk
    resp = client.post(
        "/admin/reset-user",
        json={"email": "victim@navi.test"},
        headers={"X-Admin-Key": settings.secret_key},
    )
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True


# ──────────────────────────────────────────────────────────────────
# TC-AUTH-013  Admin endpoint rejects wrong key
# ──────────────────────────────────────────────────────────────────
def test_admin_reset_rejects_wrong_key(client: TestClient):
    resp = client.post(
        "/admin/reset-user",
        json={"email": "anyone@navi.test"},
        headers={"X-Admin-Key": "totally-wrong-key"},
    )
    assert resp.status_code == 403


# ──────────────────────────────────────────────────────────────────
# TC-AUTH-014  Admin reset returns false for non-existent user
# ──────────────────────────────────────────────────────────────────
def test_admin_reset_nonexistent_user_returns_false(client: TestClient):
    from app.config import get_settings
    resp = client.post(
        "/admin/reset-user",
        json={"email": "nobody@navi.test"},
        headers={"X-Admin-Key": get_settings().secret_key},
    )
    assert resp.status_code == 200
    assert resp.json()["deleted"] is False
