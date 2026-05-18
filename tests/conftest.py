<<<<<<< HEAD
=======
"""
Navi MVP – QA Test Suite
Conftest: shared fixtures, DB setup, and test client factory.

Requirements (install before running):
    pip install pytest pytest-cov httpx fastapi psycopg2-binary passlib python-jose
               pydantic-settings pydantic[email] openai --break-system-packages

Run with:
    cd navimvp
    DATABASE_HOST=localhost DATABASE_NAME=navimvp_test DATABASE_USER=navimvp \
    DATABASE_PASSWORD=navimvppw DATABASE_PORT=5432 \
    SECRET_KEY=test-secret-key-for-qa \
    OPENAI_API_KEY=dummy \
    pytest tests/ -v --tb=short
"""

>>>>>>> bfa62b0 (Add Obsidian workspace and AI architecture files)
import os
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

<<<<<<< HEAD
os.environ.setdefault('DATABASE_HOST', 'localhost')
os.environ.setdefault('DATABASE_NAME', 'navimvp_test')
os.environ.setdefault('DATABASE_USER', 'navimvp')
os.environ.setdefault('DATABASE_PASSWORD', 'navimvppw')
os.environ.setdefault('DATABASE_PORT', '5432')
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-qa-testing-only')
os.environ.setdefault('ALGORITHM', 'HS256')
os.environ.setdefault('OPENAI_API_KEY', 'sk-test-dummy')
os.environ.setdefault('ACCOUNT_SID', 'test-sid')
os.environ.setdefault('AUTH_TOKEN', 'test-token')
os.environ.setdefault('TWILIO_NUMBER', 'whatsapp:+5511999999999')

=======
# ------------------------------------------------------------------
# Override settings BEFORE importing the app so lru_cache picks
# up the test values.
# ------------------------------------------------------------------
os.environ.setdefault("DATABASE_HOST", "localhost")
os.environ.setdefault("DATABASE_NAME", "navimvp_test")
os.environ.setdefault("DATABASE_USER", "navimvp")
os.environ.setdefault("DATABASE_PASSWORD", "navimvppw")
os.environ.setdefault("DATABASE_PORT", "5432")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-qa-testing-only")
os.environ.setdefault("ALGORITHM", "HS256")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-dummy")
os.environ.setdefault("ACCOUNT_SID", "test-sid")
os.environ.setdefault("AUTH_TOKEN", "test-token")
os.environ.setdefault("TWILIO_NUMBER", "whatsapp:+5511999999999")

# Clear lru_cache so settings reload from env
>>>>>>> bfa62b0 (Add Obsidian workspace and AI architecture files)
from app.config import get_settings  # noqa: E402
get_settings.cache_clear()

from app.main import app  # noqa: E402
from app.db import init_db, get_cursor  # noqa: E402


<<<<<<< HEAD
def _reset_test_db():
    tables = [
        'orcamento_alertas', 'orcamentos', 'transacoes',
        'confirmacoes_pendentes', 'custos_mensais', 'faturas_cartao',
        'cartoes_usuario', 'documentos_financeiros', 'perfil_financeiro',
        'configuracoes_usuario', 'usuarios',
    ]
    with get_cursor() as (conn, cursor):
        for table in tables:
            cursor.execute(f'DROP TABLE IF EXISTS {table} CASCADE')
        conn.commit()


@pytest.fixture(scope='session')
def db_setup():
=======
# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _reset_test_db() -> None:
    """Drop all application tables so each test starts clean."""
    tables = [
        "orcamento_alertas",
        "orcamentos",
        "transacoes",
        "confirmacoes_pendentes",
        "custos_mensais",
        "faturas_cartao",
        "cartoes_usuario",
        "documentos_financeiros",
        "perfil_financeiro",
        "configuracoes_usuario",
        "usuarios",
    ]
    with get_cursor() as (conn, cursor):
        for table in tables:
            cursor.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        conn.commit()


# ------------------------------------------------------------------
# Session-scoped DB setup
# ------------------------------------------------------------------

@pytest.fixture(scope="session")
def db_setup():
    """Create the schema once for the entire test session."""
>>>>>>> bfa62b0 (Add Obsidian workspace and AI architecture files)
    try:
        _reset_test_db()
        init_db()
        yield
        _reset_test_db()
    except Exception as exc:
<<<<<<< HEAD
        pytest.skip(f'PostgreSQL unavailable: {exc}')


@pytest.fixture
def client(db_setup):
=======
        pytest.skip(f"PostgreSQL unavailable – skipping DB tests: {exc}")


# ------------------------------------------------------------------
# Function-scoped client (clean DB per test)
# ------------------------------------------------------------------

@pytest.fixture
def client(db_setup):
    """Returns a TestClient with a freshly reset database."""
>>>>>>> bfa62b0 (Add Obsidian workspace and AI architecture files)
    _reset_test_db()
    init_db()
    with TestClient(app) as c:
        yield c


<<<<<<< HEAD
def auth_header(client, email='qa@navi.test', password='qapass123'):
    client.post('/register', json={'email': email, 'senha': password})
    resp = client.post('/login', json={'email': email, 'senha': password})
    token = resp.json()['token']
    return {'Authorization': f'Bearer {token}'}


def mock_openai_transaction(text):
    mock = MagicMock()
    mock.choices[0].message.content = '[{"tipo":"despesa","categoria":"alimentacao","valor":50.0}]'
    return mock


MOCK_OPENAI_PATCH = 'app.services.chat.OpenAI'
MOCK_ONBOARDING_OPENAI_PATCH = 'app.services.onboarding.OpenAI'

=======
# ------------------------------------------------------------------
# Helper: register + login, return auth header
# ------------------------------------------------------------------

def auth_header(client: TestClient, email: str = "qa@navi.test", password: str = "qapass123") -> dict:
    client.post("/register", json={"email": email, "senha": password})
    resp = client.post("/login", json={"email": email, "senha": password})
    token = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}


# ------------------------------------------------------------------
# Mock for OpenAI (used by chat.py and onboarding.py)
# ------------------------------------------------------------------

def mock_openai_transaction(text: str):
    """Returns a minimal OpenAI response that classifies as a despesa."""
    mock = MagicMock()
    mock.choices[0].message.content = (
        '[{"tipo":"despesa","categoria":"alimentacao","valor":50.0}]'
    )
    return mock


MOCK_OPENAI_PATCH = "app.services.chat.OpenAI"
MOCK_ONBOARDING_OPENAI_PATCH = "app.services.onboarding.OpenAI"

# ------------------------------------------------------------------
# Register harness fixtures globally so all test files can use
# onboarded_user, user_with_budget, user_with_transactions without
# a local import.
# ------------------------------------------------------------------
>>>>>>> bfa62b0 (Add Obsidian workspace and AI architecture files)
from tests.harness import (  # noqa: E402, F401
    onboarded_user,
    user_with_budget,
    user_with_transactions,
)
