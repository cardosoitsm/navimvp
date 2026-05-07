import os
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

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

from app.config import get_settings  # noqa: E402
get_settings.cache_clear()

from app.main import app  # noqa: E402
from app.db import init_db, get_cursor  # noqa: E402


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
    try:
        _reset_test_db()
        init_db()
        yield
        _reset_test_db()
    except Exception as exc:
        pytest.skip(f'PostgreSQL unavailable: {exc}')


@pytest.fixture
def client(db_setup):
    _reset_test_db()
    init_db()
    with TestClient(app) as c:
        yield c


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

from tests.harness import (  # noqa: E402, F401
    onboarded_user,
    user_with_budget,
    user_with_transactions,
)
