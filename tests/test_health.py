"""
Tests – Infrastructure & Health
Covers: root page, /health endpoint, DB connectivity.
"""

from fastapi.testclient import TestClient


# ──────────────────────────────────────────────────────────────────
# TC-INFRA-001  Root endpoint returns HTML
# ──────────────────────────────────────────────────────────────────
def test_root_returns_html(client: TestClient):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Navi MVP" in resp.text


# ──────────────────────────────────────────────────────────────────
# TC-INFRA-002  Health endpoint returns ok when DB is up
# ──────────────────────────────────────────────────────────────────
def test_health_ok_with_db(client: TestClient):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


# ──────────────────────────────────────────────────────────────────
# TC-INFRA-003  Health endpoint structure is correct
# ──────────────────────────────────────────────────────────────────
def test_health_response_has_required_fields(client: TestClient):
    resp = client.get("/health")
    body = resp.json()
    assert "status" in body
    assert "database" in body
    assert "environment" in body


# ──────────────────────────────────────────────────────────────────
# TC-INFRA-004  Webhook fallback returns XML
# ──────────────────────────────────────────────────────────────────
def test_webhook_fallback_returns_xml(client: TestClient):
    resp = client.post("/webhook-fallback")
    assert resp.status_code == 200
    assert "xml" in resp.headers["content-type"].lower()
    assert "<Response>" in resp.text or "Response" in resp.text


# ──────────────────────────────────────────────────────────────────
# TC-INFRA-005  Webhook fallback also works on GET
# ──────────────────────────────────────────────────────────────────
def test_webhook_fallback_get_returns_xml(client: TestClient):
    resp = client.get("/webhook-fallback")
    assert resp.status_code == 200
    assert "xml" in resp.headers["content-type"].lower()
